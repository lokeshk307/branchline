import Dexie, { type Table } from "dexie";
import MiniSearch from "minisearch";
import type { Task } from "./api";

class BranchlineDB extends Dexie {
  tasks!: Table<Task, string>;

  constructor() {
    super("branchline");
    this.version(1).stores({ tasks: "id, workspace_id, number, state" });
  }
}

export const db = new BranchlineDB();

export async function hydrateTasks(workspaceId: string, tasks: Task[]) {
  await db.tasks.where("workspace_id").equals(workspaceId).delete();
  if (tasks.length) await db.tasks.bulkPut(tasks);
}

export async function cachedTasks(workspaceId: string) {
  return db.tasks.where("workspace_id").equals(workspaceId).toArray();
}

export function searchTasks(tasks: Task[], query: string) {
  if (!query.trim()) return tasks;
  const index = new MiniSearch<Task>({
    fields: ["title", "description", "git_branch"],
    storeFields: ["id"],
    idField: "id",
  });
  index.addAll(tasks);
  const hits = new Set(index.search(query, { prefix: true, fuzzy: 0.2 }).map((hit) => hit.id));
  return tasks.filter((task) => hits.has(task.id));
}
