import createClient from "openapi-fetch";
import type { components, paths } from "./api-types";

export type Task = components["schemas"]["TaskOut"];
export type User = components["schemas"]["UserOut"];
export type Workspace = components["schemas"]["WorkspaceOut"];
export type Signal = components["schemas"]["SignalOut"];
export type Suggestion = components["schemas"]["SuggestionOut"];
export type Delivery = components["schemas"]["DeliveryOut"];
export type GitHubConnection = components["schemas"]["GitHubConnectionOut"];
export type JiraConnection = components["schemas"]["JiraOut"];

export const client = createClient<paths>({ baseUrl: "", credentials: "include" });

async function unwrap<T>(pending: Promise<unknown>): Promise<T> {
  const result = (await pending) as { data?: T; error?: unknown; response: Response };
  if (!result.response.ok) {
    const detail =
      result.error && typeof result.error === "object" && "detail" in result.error
        ? String((result.error as { detail: unknown }).detail)
        : result.response.statusText;
    throw new Error(detail || "Request failed");
  }
  return result.data as T;
}

export const api = {
  me: (): Promise<User> => unwrap(client.GET("/api/me")),
  devLogin: (login: string): Promise<User> => unwrap(client.POST("/api/auth/dev", { body: { login } })),
  githubLoginUrl: (): Promise<{ url: string }> => unwrap(client.GET("/api/auth/github/login")),
  logout: (): Promise<{ ok: boolean }> => unwrap(client.POST("/api/auth/logout")),
  workspaces: (): Promise<Workspace[]> => unwrap(client.GET("/api/workspaces")),
  createWorkspace: (name: string): Promise<Workspace> => unwrap(client.POST("/api/workspaces", { body: { name } })),
  members: (workspaceId: string): Promise<User[]> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/members", { params: { path: { workspace_id: workspaceId } } })),
  addMember: (workspaceId: string, login: string): Promise<User> =>
    unwrap(client.POST("/api/workspaces/{workspace_id}/members", { params: { path: { workspace_id: workspaceId } }, body: { login } })),
  tasks: (workspaceId: string): Promise<Task[]> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/tasks", { params: { path: { workspace_id: workspaceId } } })),
  createTask: (workspaceId: string, title: string, description: string): Promise<Task> =>
    unwrap(client.POST("/api/workspaces/{workspace_id}/tasks", { params: { path: { workspace_id: workspaceId } }, body: { title, description, state: "todo" } })),
  patchTask: (taskId: string, body: components["schemas"]["TaskPatch"]): Promise<Task> =>
    unwrap(client.PATCH("/api/tasks/{task_id}", { params: { path: { task_id: taskId } }, body })),
  signals: (taskId: string): Promise<Signal[]> =>
    unwrap(client.GET("/api/tasks/{task_id}/signals", { params: { path: { task_id: taskId } } })),
  inbox: (workspaceId: string): Promise<Signal[]> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/inbox", { params: { path: { workspace_id: workspaceId } } })),
  dismiss: (signalId: string): Promise<Signal> =>
    unwrap(client.POST("/api/signals/{signal_id}/dismiss", { params: { path: { signal_id: signalId } } })),
  now: (workspaceId: string): Promise<{ yours: Task[]; reviews: Task[]; failing: Task[] }> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/now", { params: { path: { workspace_id: workspaceId } } })),
  delivery: (workspaceId: string): Promise<Delivery> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/delivery", { params: { path: { workspace_id: workspaceId } } })),
  standup: (workspaceId: string): Promise<{ text: string }> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/standup", { params: { path: { workspace_id: workspaceId } } })),
  exportTasks: (workspaceId: string): Promise<{ tasks: Task[] }> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/export", { params: { path: { workspace_id: workspaceId } } })),
  github: (workspaceId: string): Promise<GitHubConnection> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/github", { params: { path: { workspace_id: workspaceId } } })),
  installUrl: (workspaceId: string): Promise<{ url: string }> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/github/install-url", { params: { path: { workspace_id: workspaceId } } })),
  saveGithub: (workspaceId: string, repos: string[], installationId?: number): Promise<GitHubConnection> =>
    unwrap(
      client.POST("/api/workspaces/{workspace_id}/github", {
        params: { path: { workspace_id: workspaceId } },
        body: { repos, installation_id: installationId ?? null, account_login: "" },
      }),
    ),
  importGithub: (workspaceId: string): Promise<{ created: number; updated: number; mode: string }> =>
    unwrap(client.POST("/api/workspaces/{workspace_id}/github/import", { params: { path: { workspace_id: workspaceId } } })),
  simulate: (workspaceId: string, event: string, payload: Record<string, unknown>): Promise<Task | null> =>
    unwrap(
      client.POST("/api/workspaces/{workspace_id}/github/simulate", {
        params: { path: { workspace_id: workspaceId } },
        body: { event, payload },
      }),
    ),
  jira: (workspaceId: string): Promise<JiraConnection> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/jira", { params: { path: { workspace_id: workspaceId } } })),
  connectJira: (workspaceId: string, body: components["schemas"]["JiraIn"]): Promise<JiraConnection> =>
    unwrap(client.POST("/api/workspaces/{workspace_id}/jira", { params: { path: { workspace_id: workspaceId } }, body })),
  disconnectJira: (workspaceId: string): Promise<{ ok: boolean }> =>
    unwrap(client.DELETE("/api/workspaces/{workspace_id}/jira", { params: { path: { workspace_id: workspaceId } } })),
  suggestions: (workspaceId: string): Promise<Suggestion[]> =>
    unwrap(client.GET("/api/workspaces/{workspace_id}/suggestions", { params: { path: { workspace_id: workspaceId } } })),
  accept: (id: string): Promise<Suggestion> =>
    unwrap(client.POST("/api/suggestions/{suggestion_id}/accept", { params: { path: { suggestion_id: id } } })),
  reject: (id: string): Promise<Suggestion> =>
    unwrap(client.POST("/api/suggestions/{suggestion_id}/reject", { params: { path: { suggestion_id: id } } })),
};
