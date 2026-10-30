import { Command } from "cmdk";
import { useVirtualizer } from "@tanstack/react-virtual";
import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Delivery, type Signal, type Suggestion, type Task, type User, type Workspace } from "./api";
import { cachedTasks, hydrateTasks, searchTasks } from "./cache";

type View = "now" | "board" | "inbox" | "delivery" | "settings";

const COLUMNS = [
  ["todo", "To do", "1"],
  ["in_progress", "In progress", "2"],
  ["in_review", "In review", "3"],
  ["done", "Done", "4"],
] as const;

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [view, setView] = useState<View>("now");
  const [selected, setSelected] = useState<string | null>(null);
  const [openTask, setOpenTask] = useState<Task | null>(null);
  const [palette, setPalette] = useState(false);
  const [creating, setCreating] = useState(false);
  const [moved, setMoved] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [inbox, setInbox] = useState<Signal[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [hint, setHint] = useState("");

  const refresh = useCallback(async (workspaceId: string) => {
    const [nextTasks, nextInbox, nextSuggestions] = await Promise.all([
      api.tasks(workspaceId),
      api.inbox(workspaceId),
      api.suggestions(workspaceId),
    ]);
    setTasks(nextTasks);
    setInbox(nextInbox);
    setSuggestions(nextSuggestions.filter((item) => item.status === "pending"));
    await hydrateTasks(workspaceId, nextTasks);
  }, []);

  useEffect(() => {
    api.me().then(async (me) => {
      setUser(me);
      const spaces = await api.workspaces();
      if (spaces[0]) {
        setWorkspace(spaces[0]);
        const cached = await cachedTasks(spaces[0].id);
        if (cached.length) setTasks(cached);
        await refresh(spaces[0].id);
      }
    }).catch(() => setUser(null)).finally(() => setReady(true));
  }, [refresh]);

  useEffect(() => {
    if (!workspace) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${location.host}/api/ws/${workspace.id}`);
    socket.onmessage = (event) => {
      const body = JSON.parse(event.data) as { type: string; task_id: string; state: string };
      if (body.type !== "task") return;
      setMoved(body.task_id);
      void refresh(workspace.id);
    };
    return () => socket.close();
  }, [workspace, refresh]);

  const ordered = useMemo(() => [...tasks].sort((a, b) => a.number - b.number), [tasks]);
  const selectedTask = ordered.find((task) => task.id === selected) ?? null;

  const patch = useCallback(async (task: Task, body: { state?: string; description?: string; title?: string; priority?: string }) => {
    if (body.state) setMoved(task.id);
    await api.patchTask(task.id, body);
    if (workspace) await refresh(workspace.id);
  }, [refresh, workspace]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const typing = target.tagName === "INPUT" || target.tagName === "TEXTAREA";
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette(true);
        return;
      }
      if (typing || !workspace) return;
      if (event.key === "c") setCreating(true);
      if (event.key === "j" || event.key === "k") {
        const index = Math.max(0, ordered.findIndex((task) => task.id === selected));
        const next = ordered[event.key === "j" ? Math.min(ordered.length - 1, index + 1) : Math.max(0, index - 1)];
        if (next) setSelected(next.id);
      }
      if (event.key === "Enter" && selectedTask) setOpenTask(selectedTask);
      const stateIndex = Number(event.key) - 1;
      if (selectedTask && stateIndex >= 0 && stateIndex < COLUMNS.length) {
        void patch(selectedTask, { state: COLUMNS[stateIndex][0] });
      }
      if (event.key === "g") {
        const next = (follow: KeyboardEvent) => {
          const map: Record<string, View> = { n: "now", b: "board", i: "inbox", d: "delivery", s: "settings" };
          const destination = map[follow.key];
          if (destination) setView(destination);
          window.removeEventListener("keydown", next);
        };
        window.addEventListener("keydown", next, { once: true });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ordered, patch, selected, selectedTask, workspace]);

  useEffect(() => {
    if (localStorage.getItem("branchline.hint")) return;
    const created = tasks.find((task) => task.state_source === "git" && task.state_reason.startsWith("Created from branch"));
    if (created) setHint(created.state_reason);
  }, [tasks]);

  if (!ready) return <main className="p-8 text-zinc-400">Opening Branchline…</main>;
  if (!user) return <SignIn onUser={setUser} />;
  if (!workspace) return <NameWorkspace user={user} onWorkspace={setWorkspace} />;

  const showOnboarding = tasks.length === 0 && view !== "settings";

  return (
    <div className="flex min-h-screen">
      <nav className="flex w-44 shrink-0 flex-col gap-1 border-r border-zinc-800 p-3">
        <div className="px-2 pb-4 text-sm font-semibold tracking-tight">Branchline</div>
        {([
          ["now", "Now", "G N"],
          ["board", "Board", "G B"],
          ["inbox", "Inbox", "G I"],
          ["delivery", "Delivery", "G D"],
          ["settings", "Settings", "G S"],
        ] as const).map(([id, label, shortcut]) => (
          <button key={id} title={shortcut} className={`rounded px-2 py-1.5 text-left text-sm ${view === id ? "bg-zinc-800 text-white" : "text-zinc-400 hover:bg-zinc-900"}`} onClick={() => setView(id)}>
            {label}
            {id === "inbox" && inbox.length > 0 ? <span className="ml-2 text-emerald-300">{inbox.length}</span> : null}
          </button>
        ))}
        <div className="mt-auto px-2 text-xs text-zinc-500">{user.login}</div>
      </nav>
      <main className="min-w-0 flex-1 p-6">
        {error ? <p className="mb-3 text-sm text-red-300">{error}</p> : null}
        {hint ? (
          <div className="mb-4 flex items-center justify-between rounded border border-emerald-900 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-100">
            <span>{hint}</span>
            <button onClick={() => { localStorage.setItem("branchline.hint", "1"); setHint(""); }}>Got it</button>
          </div>
        ) : null}
        {showOnboarding && view === "now" ? (
          <Onboarding workspace={workspace} onDone={() => refresh(workspace.id)} onError={setError} onManual={() => setCreating(true)} />
        ) : null}
        {view === "now" && !showOnboarding ? <Now workspaceId={workspace.id} tasks={tasks} onOpen={setOpenTask} /> : null}
        {view === "board" ? (
          <Board tasks={ordered} selected={selected} moved={moved} onSelect={setSelected} onOpen={setOpenTask} />
        ) : null}
        {view === "inbox" ? <Inbox items={inbox} onDismiss={async (id) => { await api.dismiss(id); await refresh(workspace.id); }} /> : null}
        {view === "delivery" ? <DeliveryView workspaceId={workspace.id} /> : null}
        {view === "settings" ? <Settings workspace={workspace} onChanged={() => refresh(workspace.id)} /> : null}
      </main>
      <Palette
        open={palette}
        tasks={tasks}
        onClose={() => setPalette(false)}
        onView={setView}
        onCreate={() => { setPalette(false); setCreating(true); }}
        onOpen={(task) => { setPalette(false); setOpenTask(task); }}
      />
      <TaskDialog
        task={openTask}
        suggestions={suggestions.filter((item) => item.source_task_id === openTask?.id)}
        onClose={() => setOpenTask(null)}
        onPatch={async (body) => { if (openTask) { const saved = await api.patchTask(openTask.id, body); setOpenTask(saved); await refresh(workspace.id); } }}
        onDecide={async (id, accept) => { if (accept) await api.accept(id); else await api.reject(id); await refresh(workspace.id); }}
      />
      <CreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={async (title, description) => {
          const task = await api.createTask(workspace.id, title, description);
          setCreating(false);
          await refresh(workspace.id);
          setOpenTask(task);
        }}
      />
    </div>
  );
}

function SignIn({ onUser }: { onUser: (user: User) => void }) {
  const [login, setLogin] = useState("");
  const [error, setError] = useState("");
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
      <h1 className="text-2xl font-semibold">Branchline</h1>
      <p className="text-sm text-zinc-400">The board updates when the work does.</p>
      <button
        className="rounded bg-white px-3 py-2 text-sm text-black"
        onClick={async () => {
          try {
            const { url } = await api.githubLoginUrl();
            location.href = url;
          } catch (err) {
            setError(err instanceof Error ? err.message : "GitHub sign-in is not configured");
          }
        }}
      >
        Continue with GitHub
      </button>
      <form
        className="flex gap-2"
        onSubmit={async (event) => {
          event.preventDefault();
          onUser(await api.devLogin(login));
        }}
      >
        <input className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm" placeholder="Name, for a local session" value={login} onChange={(event) => setLogin(event.target.value)} />
        <button className="rounded border border-zinc-700 px-3 py-2 text-sm" title="Local sign-in">Enter</button>
      </form>
      {error ? <p className="text-sm text-amber-200">{error}</p> : null}
    </main>
  );
}

function NameWorkspace({ user, onWorkspace }: { user: User; onWorkspace: (workspace: Workspace) => void }) {
  const [name, setName] = useState(`${user.login}'s board`);
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
      <h1 className="text-xl font-semibold">Name the workspace</h1>
      <form className="flex gap-2" onSubmit={async (event) => { event.preventDefault(); onWorkspace(await api.createWorkspace(name)); }}>
        <input className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-3 py-2" value={name} onChange={(event) => setName(event.target.value)} />
        <button className="rounded bg-white px-3 py-2 text-sm text-black">Create</button>
      </form>
    </main>
  );
}

function Onboarding({ workspace, onDone, onError, onManual }: { workspace: Workspace; onDone: () => Promise<void>; onError: (message: string) => void; onManual: () => void }) {
  const [step, setStep] = useState(1);
  const [repo, setRepo] = useState("acme/web");
  const [installation, setInstallation] = useState("");
  const [waiting, setWaiting] = useState(false);

  return (
    <section className="mx-auto max-w-xl">
      <p className="text-xs uppercase tracking-wide text-zinc-500">Step {step} of 3</p>
      <h1 className="mt-2 text-2xl font-semibold">Connect GitHub, pick repos, see the board</h1>
      {step === 1 ? (
        <div className="mt-6 flex flex-col gap-3">
          <button className="w-fit rounded bg-white px-3 py-2 text-sm text-black" onClick={async () => {
            const { url } = await api.installUrl(workspace.id);
            if (url) window.open(url, "_blank");
            setStep(2);
          }}>Connect GitHub</button>
          <button className="w-fit text-sm text-zinc-400" onClick={onManual}>Create a task by hand</button>
        </div>
      ) : null}
      {step === 2 ? (
        <form className="mt-6 flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          await api.saveGithub(workspace.id, repo.split(",").map((item) => item.trim()).filter(Boolean), installation ? Number(installation) : undefined);
          setStep(3);
        }}>
          <label className="text-sm text-zinc-400">Repos, comma separated</label>
          <input className="rounded border border-zinc-700 bg-zinc-950 px-3 py-2" value={repo} onChange={(event) => setRepo(event.target.value)} />
          <label className="text-sm text-zinc-400">Installation id, if the GitHub App is already installed</label>
          <input className="rounded border border-zinc-700 bg-zinc-950 px-3 py-2" value={installation} onChange={(event) => setInstallation(event.target.value)} />
          <button className="w-fit rounded bg-white px-3 py-2 text-sm text-black">Save repos</button>
        </form>
      ) : null}
      {step === 3 ? (
        <div className="mt-6 flex flex-col gap-3">
          <button className="w-fit rounded bg-white px-3 py-2 text-sm text-black" onClick={async () => {
            try {
              const result = await api.importGithub(workspace.id);
              if (result.mode === "imported") await onDone();
              else setWaiting(true);
            } catch (err) {
              onError(err instanceof Error ? err.message : "Import failed");
            }
          }}>Import pull requests</button>
          {waiting ? (
            <div className="rounded border border-zinc-800 p-3 text-sm text-zinc-300">
              <p>Create a branch, then Branchline will pick it up. Or replay that event here.</p>
              <button className="mt-3 rounded border border-zinc-700 px-3 py-2" onClick={async () => {
                await api.simulate(workspace.id, "create", {
                  ref: "fix/login-bug",
                  ref_type: "branch",
                  repository: { full_name: repo.split(",")[0].trim(), default_branch: "main" },
                });
                await onDone();
              }}>Replay branch fix/login-bug</button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Now({ workspaceId, tasks, onOpen }: { workspaceId: string; tasks: Task[]; onOpen: (task: Task) => void }) {
  const [data, setData] = useState<{ yours: Task[]; reviews: Task[]; failing: Task[] } | null>(null);
  useEffect(() => { void api.now(workspaceId).then(setData); }, [workspaceId, tasks]);
  if (!data) return null;
  const sections = [
    ["Your open work", data.yours.length ? data.yours : tasks.filter((task) => task.state !== "done")],
    ["Reviews waiting on you", data.reviews],
    ["Failing checks", data.failing],
  ] as const;
  return (
    <div className="grid gap-6">
      {sections.map(([title, rows]) => (
        <section key={title}>
          <h2 className="mb-2 text-sm text-zinc-400">{title}</h2>
          {rows.length === 0 ? <p className="text-sm text-zinc-600">Nothing here</p> : rows.map((task) => <TaskRow key={task.id} task={task} onOpen={onOpen} />)}
        </section>
      ))}
    </div>
  );
}

function TaskRow({ task, onOpen }: { task: Task; onOpen: (task: Task) => void }) {
  return (
    <button className="flex w-full items-baseline justify-between border-b border-zinc-900 py-2 text-left" onClick={() => onOpen(task)}>
      <span><span className="mr-2 text-zinc-500">BL-{task.number}</span>{task.title}</span>
      <span className="text-xs text-zinc-500">{task.state_reason}</span>
    </button>
  );
}

function Board({ tasks, selected, moved, onSelect, onOpen }: { tasks: Task[]; selected: string | null; moved: string | null; onSelect: (id: string) => void; onOpen: (task: Task) => void }) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {COLUMNS.map(([state, label, shortcut]) => (
        <Column key={state} label={label} shortcut={shortcut} tasks={tasks.filter((task) => task.state === state)} selected={selected} moved={moved} onSelect={onSelect} onOpen={onOpen} />
      ))}
    </div>
  );
}

function Column({ label, shortcut, tasks, selected, moved, onSelect, onOpen }: { label: string; shortcut: string; tasks: Task[]; selected: string | null; moved: string | null; onSelect: (id: string) => void; onOpen: (task: Task) => void }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({ count: tasks.length, getScrollElement: () => parentRef.current, estimateSize: () => 96, overscan: 8 });
  return (
    <section className="min-h-[70vh] rounded border border-zinc-800">
      <header className="flex items-center justify-between px-3 py-2 text-xs text-zinc-400">
        <span>{label}</span>
        <span title={`Move the selected card with ${shortcut}`}><kbd>{shortcut}</kbd> {tasks.length}</span>
      </header>
      <div ref={parentRef} className="h-[68vh] overflow-auto px-2">
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => {
            const task = tasks[item.index];
            return (
              <button
                key={task.id}
                className={`absolute left-0 right-0 rounded border px-3 py-2 text-left ${selected === task.id ? "border-emerald-700" : "border-zinc-800"} ${moved === task.id ? "card-just-moved" : ""}`}
                style={{ transform: `translateY(${item.start}px)` }}
                onClick={() => { onSelect(task.id); onOpen(task); }}
              >
                <div className="text-xs text-zinc-500">BL-{task.number}</div>
                <div className="text-sm">{task.title}</div>
                <div className="mt-1 text-xs text-emerald-200/80">{task.state_reason}</div>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function Inbox({ items, onDismiss }: { items: Signal[]; onDismiss: (id: string) => Promise<void> }) {
  return (
    <section>
      <h1 className="mb-4 text-xl">Inbox</h1>
      <p className="mb-4 text-sm text-zinc-500">Signals that did not match a task stay here, off the board.</p>
      {items.length === 0 ? <p className="text-sm text-zinc-600">Inbox is clear</p> : items.map((item) => (
        <div key={item.id} className="flex items-center justify-between border-b border-zinc-900 py-2 text-sm">
          <span>{item.summary}</span>
          <button className="text-zinc-400" onClick={() => void onDismiss(item.id)}>Dismiss</button>
        </div>
      ))}
    </section>
  );
}

function DeliveryView({ workspaceId }: { workspaceId: string }) {
  const [data, setData] = useState<Delivery | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { void api.delivery(workspaceId).then(setData); }, [workspaceId]);
  if (!data) return null;
  const groups = [
    ["In progress", data.in_progress],
    ["In review", data.in_review],
    ["Done this week", data.done],
    ["Blocked", data.blocked],
  ] as const;
  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl">Delivery</h1>
        <button className="rounded border border-zinc-700 px-3 py-1.5 text-sm" onClick={async () => {
          const { text } = await api.standup(workspaceId);
          await navigator.clipboard.writeText(text);
          setCopied(true);
        }}>{copied ? "Copied" : "Copy standup"}</button>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        {groups.map(([title, rows]) => (
          <div key={title}>
            <h2 className="mb-2 text-sm text-zinc-400">{title}</h2>
            {rows.length === 0 ? <p className="text-sm text-zinc-600">Nothing here</p> : rows.map((row) => (
              <div key={`${title}-${row.id}`} className="border-b border-zinc-900 py-2 text-sm">
                <div>BL-{row.number} {row.title}</div>
                <div className="text-xs text-zinc-500">{row.owner_label || "Unassigned"}{row.stall_reason ? ` — ${row.stall_reason}` : ""}</div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function Settings({ workspace, onChanged }: { workspace: Workspace; onChanged: () => Promise<void> }) {
  const [jira, setJira] = useState({ site: "", email: "", api_token: "", project_key: "", todo: "To Do", in_progress: "In Progress", in_review: "In Review", done: "Done" });
  const [connected, setConnected] = useState(false);
  const [member, setMember] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { void api.jira(workspace.id).then((row) => setConnected(row.connected)); }, [workspace.id]);
  return (
    <section className="max-w-xl">
      <h1 className="mb-4 text-xl">Settings</h1>
      <p className="mb-6 text-sm text-zinc-400">CLI token for <span className="text-zinc-200">branchline link</span>: {workspace.cli_token}</p>
      <form className="grid gap-2" onSubmit={async (event) => {
        event.preventDefault();
        await api.addMember(workspace.id, member);
        setMember("");
        setMessage("Member added");
      }}>
        <label className="text-sm text-zinc-400">Add a member</label>
        <div className="flex gap-2">
          <input className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-3 py-2" value={member} onChange={(event) => setMember(event.target.value)} />
          <button className="rounded border border-zinc-700 px-3">Add</button>
        </div>
      </form>
      <form className="mt-8 grid gap-2" onSubmit={async (event) => {
        event.preventDefault();
        await api.connectJira(workspace.id, {
          site: jira.site,
          email: jira.email,
          api_token: jira.api_token,
          project_key: jira.project_key,
          status_map: { todo: jira.todo, in_progress: jira.in_progress, in_review: jira.in_review, done: jira.done },
        });
        setConnected(true);
        setMessage("Jira connected. The board still works if you disconnect it.");
        await onChanged();
      }}>
        <h2 className="text-sm text-zinc-300">Jira, optional</h2>
        {(["site", "email", "api_token", "project_key"] as const).map((field) => (
          <input key={field} className="rounded border border-zinc-700 bg-zinc-950 px-3 py-2" placeholder={field.replace("_", " ")} type={field === "api_token" ? "password" : "text"} value={jira[field]} onChange={(event) => setJira({ ...jira, [field]: event.target.value })} />
        ))}
        <button className="w-fit rounded bg-white px-3 py-2 text-sm text-black">Save Jira</button>
      </form>
      {connected ? <button className="mt-3 text-sm text-zinc-400" onClick={async () => { await api.disconnectJira(workspace.id); setConnected(false); }}>Disconnect Jira</button> : null}
      <button className="mt-6 block text-sm text-emerald-200" onClick={async () => {
        const body = await api.exportTasks(workspace.id);
        const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "branchline-tasks.json";
        link.click();
        URL.revokeObjectURL(url);
      }}>Export tasks</button>
      {message ? <p className="mt-4 text-sm text-zinc-400">{message}</p> : null}
    </section>
  );
}

function Palette({ open, tasks, onClose, onView, onCreate, onOpen }: { open: boolean; tasks: Task[]; onClose: () => void; onView: (view: View) => void; onCreate: () => void; onOpen: (task: Task) => void }) {
  const [query, setQuery] = useState("");
  const matches = searchTasks(tasks, query).slice(0, 8);
  return (
    <Command.Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }} label="Command palette" className="fixed inset-0 z-20 bg-black/50">
      <div className="mx-auto mt-24 max-w-lg overflow-hidden rounded border border-zinc-700 bg-zinc-950 shadow-2xl">
        <Command.Input value={query} onValueChange={setQuery} placeholder="Search tasks or jump" className="w-full border-b border-zinc-800 bg-transparent px-3 py-3 outline-none" />
        <Command.List className="max-h-80 overflow-auto p-2">
          <Command.Item onSelect={onCreate} className="rounded px-2 py-2 text-sm data-[selected=true]:bg-zinc-800">New task <kbd className="float-right">C</kbd></Command.Item>
          <Command.Item onSelect={() => { onView("now"); onClose(); }} className="rounded px-2 py-2 text-sm data-[selected=true]:bg-zinc-800">Go to Now</Command.Item>
          <Command.Item onSelect={() => { onView("board"); onClose(); }} className="rounded px-2 py-2 text-sm data-[selected=true]:bg-zinc-800">Go to Board</Command.Item>
          <Command.Item onSelect={() => { onView("delivery"); onClose(); }} className="rounded px-2 py-2 text-sm data-[selected=true]:bg-zinc-800">Go to Delivery</Command.Item>
          {matches.map((task) => (
            <Command.Item key={task.id} onSelect={() => onOpen(task)} className="rounded px-2 py-2 text-sm data-[selected=true]:bg-zinc-800">BL-{task.number} {task.title}</Command.Item>
          ))}
        </Command.List>
      </div>
    </Command.Dialog>
  );
}

function TaskDialog({ task, suggestions, onClose, onPatch, onDecide }: { task: Task | null; suggestions: Suggestion[]; onClose: () => void; onPatch: (body: { title?: string; description?: string; state?: string; priority?: string }) => Promise<void>; onDecide: (id: string, accept: boolean) => Promise<void> }) {
  const [description, setDescription] = useState("");
  const [signals, setSignals] = useState<Signal[]>([]);
  useEffect(() => {
    if (!task) return;
    setDescription(task.description);
    void api.signals(task.id).then(setSignals);
  }, [task]);
  return (
    <Dialog.Root open={task !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-10 grid max-h-[85vh] w-[min(960px,92vw)] -translate-x-1/2 grid-cols-2 overflow-auto rounded border border-zinc-700 bg-zinc-950 p-5">
          <div>
            <Dialog.Title className="text-lg">{task ? `BL-${task.number} ${task.title}` : ""}</Dialog.Title>
            <p className="mt-1 text-sm text-emerald-200/80">{task?.state_reason}</p>
            <textarea className="mt-4 h-48 w-full rounded border border-zinc-800 bg-zinc-900 p-3 text-sm" value={description} onChange={(event) => setDescription(event.target.value)} onBlur={() => void onPatch({ description })} />
            <div className="mt-3 flex gap-2 text-xs">
              {COLUMNS.map(([state, label]) => (
                <button key={state} className={`rounded border px-2 py-1 ${task?.state === state ? "border-emerald-600" : "border-zinc-800"}`} onClick={() => void onPatch({ state })}>{label}</button>
              ))}
            </div>
            <ul className="mt-4 space-y-1 text-xs text-zinc-500">
              {signals.map((signal) => <li key={signal.id}>{signal.summary}</li>)}
            </ul>
          </div>
          <aside className="border-l border-zinc-800 pl-5 text-sm">
            <h3 className="text-zinc-400">Code</h3>
            <p className="mt-2">{task?.git_branch || "No branch yet"}</p>
            {task?.pr_url ? <a className="mt-2 block text-emerald-200" href={task.pr_url}>Pull request {task.pr_number}</a> : null}
            <p className="mt-2 text-zinc-400">Checks: {task?.checks_status}</p>
            <ul className="mt-2 text-xs text-zinc-500">{(task?.changed_files ?? []).map((file) => <li key={file}>{file}</li>)}</ul>
            {suggestions.map((suggestion) => (
              <div key={suggestion.id} className="mt-4 rounded border border-zinc-800 p-3">
                <p>{suggestion.reason}</p>
                <p className="text-xs text-zinc-500">Confidence {suggestion.confidence}</p>
                <div className="mt-2 flex gap-2">
                  <button className="rounded bg-white px-2 py-1 text-xs text-black" onClick={() => void onDecide(suggestion.id, true)}>Accept</button>
                  <button className="rounded border border-zinc-700 px-2 py-1 text-xs" onClick={() => void onDecide(suggestion.id, false)}>Reject</button>
                </div>
              </div>
            ))}
          </aside>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function CreateDialog({ open, onClose, onCreate }: { open: boolean; onClose: () => void; onCreate: (title: string, description: string) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-24 w-[min(520px,92vw)] -translate-x-1/2 rounded border border-zinc-700 bg-zinc-950 p-5">
          <Dialog.Title className="mb-3 text-lg">New task</Dialog.Title>
          <form className="grid gap-3" onSubmit={async (event) => { event.preventDefault(); await onCreate(title, description); setTitle(""); setDescription(""); }}>
            <input className="rounded border border-zinc-700 bg-zinc-900 px-3 py-2" placeholder="Title" value={title} onChange={(event) => setTitle(event.target.value)} />
            <textarea className="h-32 rounded border border-zinc-700 bg-zinc-900 px-3 py-2" placeholder="Markdown" value={description} onChange={(event) => setDescription(event.target.value)} />
            <button className="w-fit rounded bg-white px-3 py-2 text-sm text-black">Create</button>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
