import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useState } from 'react'

import { scenariosApi } from '../api/client'
import type { Beat } from '../types'

import SummaryField from './SummaryField'

interface Props {
  scenarioId?: string
  beats: Beat[]
  onChange: (next: Beat[]) => void
}

export default function BeatsEditor({ scenarioId, beats, onChange }: Props) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = beats.findIndex(b => b.id === active.id)
    const newIndex = beats.findIndex(b => b.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    const moved = arrayMove(beats, oldIndex, newIndex).map((b, i) => ({ ...b, order: i }))
    onChange(moved)
  }

  function update(beatId: string, patch: Partial<Beat>) {
    onChange(beats.map(b => (b.id === beatId ? { ...b, ...patch } : b)))
  }

  function remove(beatId: string) {
    if (!confirm('Delete this beat?')) return
    onChange(beats.filter(b => b.id !== beatId).map((b, i) => ({ ...b, order: i })))
  }

  function addBeat() {
    const newBeat: Beat = {
      // The empty id signals "assign on save". The drag handle still works
      // because dnd-kit will use a temporary id below.
      id: `new-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      order: beats.length,
      name: 'New Beat',
      description: '',
      summary: '',
      summary_hash: '',
      transition_condition: '',
      starter_prompt: '',
    }
    onChange([...beats, newBeat])
  }

  if (beats.length === 0) {
    return (
      <div className="space-y-3">
        <div className="border border-gray-800 bg-gray-900 rounded p-4 text-sm text-gray-400">
          No beats defined — this scenario will use <span className="font-mono">initial message</span>{' '}
          as a single opening. Add beats below to structure the plot in scenes.
        </div>
        <button
          type="button"
          onClick={addBeat}
          className="text-sm text-blue-300 hover:text-blue-200 border border-blue-800 hover:border-blue-600 rounded px-3 py-1.5"
        >
          + Add Beat
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={beats.map(b => b.id)} strategy={verticalListSortingStrategy}>
          <div className="space-y-2">
            {beats.map((beat, idx) => (
              <BeatCard
                key={beat.id}
                beat={beat}
                index={idx}
                scenarioId={scenarioId}
                onUpdate={(patch) => update(beat.id, patch)}
                onRemove={() => remove(beat.id)}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>
      <button
        type="button"
        onClick={addBeat}
        className="text-sm text-blue-300 hover:text-blue-200 border border-blue-800 hover:border-blue-600 rounded px-3 py-1.5"
      >
        + Add Beat
      </button>
    </div>
  )
}


function BeatCard({
  beat,
  index,
  scenarioId,
  onUpdate,
  onRemove,
}: {
  beat: Beat
  index: number
  scenarioId?: string
  onUpdate: (patch: Partial<Beat>) => void
  onRemove: () => void
}) {
  const sortable = useSortable({ id: beat.id })
  const [open, setOpen] = useState(false)
  const style = {
    transform: CSS.Transform.toString(sortable.transform),
    transition: sortable.transition,
  }

  async function regenSummary(): Promise<string> {
    if (!scenarioId || !beat.id || beat.id.startsWith('new-')) {
      throw new Error('Save the scenario first to regenerate this beat\'s summary.')
    }
    const r = await scenariosApi.regenerateBeatSummary(scenarioId, beat.id)
    return r.summary
  }

  return (
    <div
      ref={sortable.setNodeRef}
      style={style}
      className="border border-gray-800 bg-gray-900 rounded"
    >
      <div className="flex items-center px-3 py-2 gap-2">
        <button
          type="button"
          {...sortable.attributes}
          {...sortable.listeners}
          className="text-gray-500 hover:text-gray-300 cursor-grab active:cursor-grabbing select-none px-1"
          title="Drag to reorder"
        >
          ⋮⋮
        </button>
        <span className="text-xs text-gray-500 font-mono w-6">#{index}</span>
        <input
          type="text"
          value={beat.name}
          onChange={e => onUpdate({ name: e.target.value })}
          className="flex-1 bg-transparent border-none text-sm font-semibold focus:outline-none"
        />
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="text-xs text-gray-400 hover:text-gray-200 px-2"
        >
          {open ? '▴' : '▾'}
        </button>
        <button
          type="button"
          onClick={onRemove}
          className="text-xs text-red-400 hover:text-red-300 px-2"
        >
          Delete
        </button>
      </div>
      {open && (
        <div className="border-t border-gray-800 p-3 space-y-3">
          <div>
            <label className="text-xs uppercase tracking-wide text-gray-400">
              Description (DM-facing)
            </label>
            <textarea
              value={beat.description}
              onChange={e => onUpdate({ description: e.target.value })}
              rows={3}
              placeholder="Environment, hazards, NPCs in this beat — DM-facing notes."
              className="w-full bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
            />
          </div>
          <SummaryField
            label="Summary (Director hint)"
            value={beat.summary}
            onChange={v => onUpdate({ summary: v })}
            onRegenerate={regenSummary}
            helper="One sentence the Director uses to decide whether to advance or skip."
          />
          <div>
            <label className="text-xs uppercase tracking-wide text-gray-400">
              Transition condition
            </label>
            <textarea
              value={beat.transition_condition}
              onChange={e => onUpdate({ transition_condition: e.target.value })}
              rows={2}
              placeholder="Plain English: what player action or story event ends this beat?"
              className="w-full bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
            />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-gray-400">
              Starter prompt
            </label>
            <textarea
              value={beat.starter_prompt}
              onChange={e => onUpdate({ starter_prompt: e.target.value })}
              rows={3}
              placeholder="Prose injected as a DM message when this beat begins. Sets the scene."
              className="w-full bg-gray-800 text-sm rounded px-2 py-1.5 border border-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-600 resize-y"
            />
          </div>
        </div>
      )}
    </div>
  )
}
