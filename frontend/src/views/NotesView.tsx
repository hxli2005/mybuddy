import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createNote, deleteNote, fetchNotes, updateNote } from "../api/client";
import {
  CancelEditButton,
  ConfirmAction,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
  Tags,
} from "../components/Primitives";
import { queryKeys } from "../state/observability";
import type { Note } from "../types/api";

export function NotesView() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.notes, queryFn: fetchNotes });
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("");
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editContent, setEditContent] = useState("");

  const refreshNotesAndMemory = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.notes });
    queryClient.invalidateQueries({ queryKey: queryKeys.memory });
  };
  const createMutation = useMutation({
    mutationFn: createNote,
    onSuccess: () => {
      setTitle("");
      setTags("");
      setContent("");
      refreshNotesAndMemory();
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({
      id,
      title,
      content,
      tags,
    }: {
      id: number;
      title?: string;
      content: string;
      tags: string[];
    }) => updateNote(id, { title, content, tags }),
    onSuccess: () => {
      setEditingId(null);
      refreshNotesAndMemory();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteNote,
    onSuccess: refreshNotesAndMemory,
  });

  const parsedTags = useMemo(() => splitTags(tags), [tags]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const clean = content.trim();
    if (!clean) return;
    createMutation.mutate({
      title: title.trim() || undefined,
      content: clean,
      tags: parsedTags,
    });
  }

  function startEdit(note: Note) {
    setEditingId(note.id);
    setEditTitle(note.title);
    setEditTags(note.tags.join(" "));
    setEditContent(note.content);
  }

  function saveEdit(noteId: number) {
    const clean = editContent.trim();
    if (!clean) return;
    updateMutation.mutate({
      id: noteId,
      title: editTitle.trim() || undefined,
      content: clean,
      tags: splitTags(editTags),
    });
  }

  if (query.isLoading) return <LoadingState label="正在读取笔记" />;
  if (query.error) return <ErrorState error={query.error} />;

  return (
    <section className="view">
      <PageHeader description="笔记会刷新记忆索引，适合把事实、计划和引用材料写入上下文。" title="笔记" />
      <div className="notes-layout">
        <Panel className="note-capture" title="写入材料" description="内容是唯一必填项；标题和标签用于之后检索。">
          <form className="note-form" onSubmit={submit}>
            <label>
              标题
              <input aria-label="标题" onChange={(event) => setTitle(event.target.value)} value={title} />
            </label>
            <label>
              标签
              <input aria-label="标签" onChange={(event) => setTags(event.target.value)} value={tags} />
            </label>
            <label className="full-field">
              内容
              <textarea aria-label="内容" onChange={(event) => setContent(event.target.value)} rows={8} value={content} />
            </label>
            <div className="note-preview">
              <span>预览标签</span>
              <Tags values={parsedTags} />
            </div>
            <button data-state={createMutation.isPending ? "loading" : undefined} disabled={createMutation.isPending || !content.trim()} type="submit">
              <Plus size={16} />
              <span>{createMutation.isPending ? "保存中" : "保存笔记"}</span>
            </button>
          </form>
        </Panel>

        <Panel title="资料库" description={`${query.data?.notes.length || 0} 条笔记`}>
          {query.data?.notes.length ? (
            <div className="notes-list">
              {query.data.notes.map((note) => (
                <NoteCard
                  deletePending={deleteMutation.isPending}
                  editing={editingId === note.id}
                  editContent={editContent}
                  editTags={editTags}
                  editTitle={editTitle}
                  key={note.id}
                  note={note}
                  onCancel={() => setEditingId(null)}
                  onDelete={() => deleteMutation.mutate(note.id)}
                  onEdit={() => startEdit(note)}
                  onEditContent={setEditContent}
                  onEditTags={setEditTags}
                  onEditTitle={setEditTitle}
                  onSave={() => saveEdit(note.id)}
                  updatePending={updateMutation.isPending}
                />
              ))}
            </div>
          ) : (
            <EmptyState title="暂无笔记" text="写入第一条笔记后，它会同时刷新记忆上下文。" />
          )}
        </Panel>
      </div>
    </section>
  );
}

function NoteCard({
  note,
  editing,
  editTitle,
  editTags,
  editContent,
  updatePending,
  deletePending,
  onEdit,
  onCancel,
  onDelete,
  onSave,
  onEditTitle,
  onEditTags,
  onEditContent,
}: {
  note: Note;
  editing: boolean;
  editTitle: string;
  editTags: string;
  editContent: string;
  updatePending: boolean;
  deletePending: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onSave: () => void;
  onEditTitle: (value: string) => void;
  onEditTags: (value: string) => void;
  onEditContent: (value: string) => void;
}) {
  return (
    <article className="list-card note-card">
      {editing ? (
        <>
          <input aria-label="笔记标题" onChange={(event) => onEditTitle(event.target.value)} value={editTitle} />
          <input aria-label="笔记标签" onChange={(event) => onEditTags(event.target.value)} value={editTags} />
          <textarea aria-label="笔记内容" onChange={(event) => onEditContent(event.target.value)} rows={5} value={editContent} />
          <div className="inline-actions">
            <button
              aria-label="保存笔记"
              className="icon-button"
              data-state={updatePending ? "loading" : undefined}
              disabled={updatePending || !editContent.trim()}
              onClick={onSave}
              title="保存"
              type="button"
            >
              <Check size={15} />
            </button>
            <CancelEditButton onClick={onCancel} label="取消编辑笔记" />
          </div>
        </>
      ) : (
        <>
          <header>
            <strong>{note.title || "无标题笔记"}</strong>
            <span>{formatDate(note.updated_at || note.created_at)}</span>
          </header>
          <p>{note.content}</p>
          <Tags values={note.tags} />
          <div className="inline-actions">
            <button aria-label="编辑笔记" className="icon-button" onClick={onEdit} title="编辑" type="button">
              <Pencil size={15} />
            </button>
            <ConfirmAction
              confirmLabel="确认删除笔记"
              disabled={deletePending}
              label="删除笔记"
              onConfirm={onDelete}
              title="删除"
            >
              <Trash2 size={15} />
            </ConfirmAction>
          </div>
        </>
      )}
    </article>
  );
}

function splitTags(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function formatDate(value: string) {
  if (!value) return "-";
  return value.slice(0, 19).replace("T", " ");
}
