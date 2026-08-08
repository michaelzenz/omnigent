import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const LAST_ROLE_KEY = "puppy-garden:last-role";

export type PuppyGardenRole = "secretary" | "broker";

export type PuppyGardenChatTarget =
  | { kind: "role"; role: PuppyGardenRole }
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

function readStoredRole(): PuppyGardenRole {
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

function writeStoredRole(role: PuppyGardenRole): void {
  try {
    localStorage.setItem(LAST_ROLE_KEY, role);
  } catch {
    // Ignore quota / privacy errors.
  }
}

export interface PuppyGardenChatContextValue {
  target: PuppyGardenChatTarget;
  homeRole: PuppyGardenRole;
  setRole: (role: PuppyGardenRole) => void;
  openManager: (taskId: string, conversationId: string | null, title: string) => void;
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

const PuppyGardenChatContext = createContext<PuppyGardenChatContextValue | null>(null);

export function PuppyGardenChatProvider({ children }: { children: ReactNode }) {
  const [homeRole, setHomeRole] = useState<PuppyGardenRole>(readStoredRole);
  const [target, setTarget] = useState<PuppyGardenChatTarget>(() => ({
    kind: "role",
    role: readStoredRole(),
  }));

  const setRole = useCallback((role: PuppyGardenRole) => {
    writeStoredRole(role);
    setHomeRole(role);
    setTarget({ kind: "role", role });
  }, []);

  const openManager = useCallback(
    (taskId: string, conversationId: string | null, title: string) => {
      setTarget({ kind: "manager", taskId, conversationId, title });
    },
    [],
  );

  const openWorker = useCallback(
    (taskId: string, workerId: string, conversationId: string | null, label: string) => {
      setTarget({ kind: "worker", taskId, workerId, conversationId, label });
    },
    [],
  );

  const dismissToRole = useCallback(() => {
    setTarget({ kind: "role", role: homeRole });
  }, [homeRole]);

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

  return (
    <PuppyGardenChatContext.Provider value={value}>{children}</PuppyGardenChatContext.Provider>
  );
}

export function usePuppyGardenChat(): PuppyGardenChatContextValue {
  const ctx = useContext(PuppyGardenChatContext);
  if (ctx === null) {
    throw new Error("usePuppyGardenChat must be used within PuppyGardenChatProvider");
  }
  return ctx;
}
