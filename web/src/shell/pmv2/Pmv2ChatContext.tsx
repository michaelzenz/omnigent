import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { acquireManagerQueueHold, releaseManagerQueueHold } from "@/lib/agentTasksApi";
import { isPmv2FixtureMode } from "./fixtures/pmv2FixtureMode";

const LAST_ROLE_KEY = "pmv2:last-role";

export type Pmv2Role = "secretary" | "broker";

export type Pmv2ChatTarget =
  | { kind: "role"; role: Pmv2Role }
  | {
      kind: "manager";
      taskId: string;
      conversationId: string | null;
      title: string;
    }
  | {
      kind: "worker";
      taskId: string;
      workerId: string;
      conversationId: string | null;
      label: string;
    };

function readStoredRole(): Pmv2Role {
  try {
    const stored = localStorage.getItem(LAST_ROLE_KEY);
    if (stored === "broker" || stored === "secretary") {
      return stored;
    }
  } catch {
    // localStorage may be unavailable in some embed contexts.
  }
  return "secretary";
}

function writeStoredRole(role: Pmv2Role): void {
  try {
    localStorage.setItem(LAST_ROLE_KEY, role);
  } catch {
    // Ignore quota / privacy errors.
  }
}

export interface Pmv2ChatContextValue {
  target: Pmv2ChatTarget;
  homeRole: Pmv2Role;
  setRole: (role: Pmv2Role) => void;
  openManager: (taskId: string, conversationId: string | null, title: string) => Promise<void>;
  openWorker: (
    taskId: string,
    workerId: string,
    conversationId: string | null,
    label: string,
  ) => void;
  dismissToRole: () => void;
  isManagerSelected: (taskId: string) => boolean;
  isWorkerSelected: (taskId: string, workerId: string) => boolean;
}

const Pmv2ChatContext = createContext<Pmv2ChatContextValue | null>(null);

export function Pmv2ChatProvider({ children }: { children: ReactNode }) {
  const [homeRole, setHomeRole] = useState<Pmv2Role>(readStoredRole);
  const [target, setTarget] = useState<Pmv2ChatTarget>(() => ({
    kind: "role",
    role: readStoredRole(),
  }));

  const managerHoldRef = useRef<{ taskId: string; token: string } | null>(null);

  const releaseManagerHold = useCallback(() => {
    const hold = managerHoldRef.current;
    managerHoldRef.current = null;
    if (hold) void releaseManagerQueueHold(hold.taskId, hold.token).catch(() => undefined);
  }, []);

  const setRole = useCallback(
    (role: Pmv2Role) => {
      releaseManagerHold();
      writeStoredRole(role);
      setHomeRole(role);
      setTarget({ kind: "role", role });
    },
    [releaseManagerHold],
  );

  const openManager = useCallback(
    async (taskId: string, conversationId: string | null, title: string) => {
      if (isPmv2FixtureMode()) {
        setTarget({ kind: "manager", taskId, conversationId, title });
        return;
      }
      const current = managerHoldRef.current;
      if (current?.taskId === taskId) {
        await acquireManagerQueueHold(taskId, current.token);
        setTarget({ kind: "manager", taskId, conversationId, title });
        return;
      }
      const hold = await acquireManagerQueueHold(taskId);
      managerHoldRef.current = { taskId, token: hold.token };
      setTarget({ kind: "manager", taskId, conversationId, title });
      if (current) {
        void releaseManagerQueueHold(current.taskId, current.token).catch(() => undefined);
      }
    },
    [],
  );

  const openWorker = useCallback(
    (taskId: string, workerId: string, conversationId: string | null, label: string) => {
      releaseManagerHold();
      setTarget({ kind: "worker", taskId, workerId, conversationId, label });
    },
    [releaseManagerHold],
  );

  const dismissToRole = useCallback(() => {
    releaseManagerHold();
    setTarget({ kind: "role", role: homeRole });
  }, [homeRole, releaseManagerHold]);

  useEffect(() => {
    const heartbeat = window.setInterval(() => {
      const hold = managerHoldRef.current;
      if (hold) void acquireManagerQueueHold(hold.taskId, hold.token).catch(releaseManagerHold);
    }, 45_000);
    return () => {
      window.clearInterval(heartbeat);
      releaseManagerHold();
    };
  }, [releaseManagerHold]);

  const isManagerSelected = useCallback(
    (taskId: string) => target.kind === "manager" && target.taskId === taskId,
    [target],
  );

  const isWorkerSelected = useCallback(
    (taskId: string, workerId: string) =>
      target.kind === "worker" && target.taskId === taskId && target.workerId === workerId,
    [target],
  );

  const value = useMemo(
    () => ({
      target,
      homeRole,
      setRole,
      openManager,
      openWorker,
      dismissToRole,
      isManagerSelected,
      isWorkerSelected,
    }),
    [
      target,
      homeRole,
      setRole,
      openManager,
      openWorker,
      dismissToRole,
      isManagerSelected,
      isWorkerSelected,
    ],
  );

  return <Pmv2ChatContext.Provider value={value}>{children}</Pmv2ChatContext.Provider>;
}

export function usePmv2Chat(): Pmv2ChatContextValue {
  const ctx = useContext(Pmv2ChatContext);
  if (ctx === null) {
    throw new Error("usePmv2Chat must be used within Pmv2ChatProvider");
  }
  return ctx;
}
