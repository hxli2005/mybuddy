import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Plus, X } from "lucide-react";
import { createNote, deleteNote, fetchNotes, updateNote } from "../../lib/api";
import { Button, EmptyState, Field, IconButton, Input, SectionLabel, Textarea } from "../../components/ui";
import { queryKeys } from "../../lib/queryKeys";
import { ConfirmDelete, ItemCard, MiniTags, SectionState } from "./common";
import { NotebookText } from "lucide-react";
import type { Note } from "../../types/api";

export function NotesSection() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.notes, queryFn: fetchNotes });
  const [title, setTitle] = useState("");
  const [tags, setTags] = useState("");
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: queryKeys.notes });
    qc.invalidateQueries({ queryKey: queryKeys.memory });
  };
  const createMutation = useMutation({
    mutationFn: createNote,
    onSuccess: () => {
      setTitle("");
      setTags("");
      setContent("");
      refresh();
    },
  });
  const deleteMutation = useMutation({ mutationFn: deleteNote, onSuccess: refresh });

  const parsedTags = useMemo(() => splitTags(tags), [tags]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const clean = content.trim();
    if (!clean) return;
    createMutation.mutate({ title: title.trim() || undefined, content: clean, tags: parsedTags });
  }

  return (
    <SectionState loading={query.isLoading} error={query.error}>
      <div className="flex flex-col gap-5">
        <form onSubmit={submit} className="flex flex-col gap-3 rounded-2xl border border-line bg-surface-2/60 p-3.5">
          <Field label="标题">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="可留空" />
          </Field>
          <Field label="标签" hint="空格或逗号分隔">
            <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="工作 灵感" />
          </Field>
          <Field label="内容">
            <Textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} placeholder="写点要记住的材料…" />
          </Field>
          <Button type="submit" disabled={createMutation.isPending || !content.trim()} className="w-full">
            <Plus size={16} />
            {createMutation.isPending ? "保存中…" : "记下来"}
          </Button>
        </form>

        <div className="flex flex-col gap-2.5">
          <SectionLabel>资料库 · {query.data?.notes.length || 0}</SectionLabel>
          {query.data?.notes.length ? (
            query.data.notes.map((note) => (
              <NoteCard
                key={note.id}
                note={note}
                editing={editingId === note.id}
                onEdit={() => setEditingId(note.id)}
                onCancel={() => setEditingId(null)}
                onSaved={() => {
                  setEditingId(null);
                  refresh();
                }}
                onDelete={() => deleteMutation.mutate(note.id)}
                deleting={deleteMutation.isPending}
              />
            ))
          ) : (
            <EmptyState icon={NotebookText} title="还没有笔记" text="写下的第一条会同时进入记忆索引。" />
          )}
        </div>
      </div>
    </SectionState>
  );
}

function NoteCard({
  note,
  editing,
  onEdit,
  onCancel,
  onSaved,
  onDelete,
  deleting,
}: {
  note: Note;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSaved: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [title, setTitle] = useState(note.title);
  const [tags, setTags] = useState(note.tags.join(" "));
  const [content, setContent] = useState(note.content);
  const mutation = useMutation({
    mutationFn: () => updateNote(note.id, { title: title.trim() || undefined, content: content.trim(), tags: splitTags(tags) }),
    onSuccess: onSaved,
  });

  if (editing) {
    return (
      <ItemCard>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="标题" />
        <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="标签" />
        <Textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
        <div className="flex items-center justify-end gap-1">
          <IconButton icon={Check} label="保存" tone="accent" disabled={mutation.isPending || !content.trim()} onClick={() => mutation.mutate()} />
          <IconButton icon={X} label="取消" onClick={onCancel} />
        </div>
      </ItemCard>
    );
  }

  return (
    <ItemCard>
      <div className="flex items-baseline justify-between gap-2">
        <strong className="text-[14px] font-semibold text-ink truncate">{note.title || "无标题"}</strong>
        <span className="text-[11px] text-faint shrink-0">{formatDate(note.updated_at || note.created_at)}</span>
      </div>
      <p className="text-[13.5px] text-ink-soft leading-relaxed whitespace-pre-wrap">{note.content}</p>
      <MiniTags values={note.tags} />
      <div className="flex items-center justify-end gap-1">
        <IconButton icon={Pencil} label="编辑" onClick={onEdit} />
        <ConfirmDelete onConfirm={onDelete} label="删除笔记" disabled={deleting} />
      </div>
    </ItemCard>
  );
}

function splitTags(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

function formatDate(value: string) {
  if (!value) return "";
  return value.slice(0, 16).replace("T", " ");
}
