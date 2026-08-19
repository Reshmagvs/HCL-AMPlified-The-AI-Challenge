/**
 * The skill map: dependency order, drawn as plain SVG.
 *
 * This replaced ReactFlow. The layout was always computed here — depth is the
 * longest prerequisite chain, so horizontal position literally means "how far
 * into the order this sits", and a physics layout would scramble the one
 * property the picture exists to show. Once the positions are ours, the library
 * was only drawing lines, and it was doing that unreliably: with 90 valid nodes
 * and 137 valid edges it rendered every node and not one edge, silently. A graph
 * with no edges is worse than no graph.
 *
 * Drawing it directly costs about a hundred lines, removes ~250 kB from the
 * bundle, makes the visual match the rest of the interface, and cannot fail
 * halfway. Pan is a drag, zoom is two buttons and the wheel.
 */

import { useMemo, useRef, useState } from 'react'
import type { GraphPayload } from '../lib/api'

const COLUMN = 200
const ROW = 54
const NODE_W = 158
const NODE_H = 38
const PAD = 40

export const MAP_COLOURS = {
  goal: '#B0563A',
  known: '#5B7A5A',
  partial: '#B08034',
  todo: '#8B8175',
  other: '#D5C9B4',
}

function colourFor(mastery: number, inPath: boolean, isGoal: boolean): string {
  if (isGoal) return MAP_COLOURS.goal
  if (mastery >= 0.7) return MAP_COLOURS.known
  if (mastery > 0) return MAP_COLOURS.partial
  return inPath ? MAP_COLOURS.todo : MAP_COLOURS.other
}

type Placed = {
  id: string
  name: string
  x: number
  y: number
  colour: string
  active: boolean
  isGoal: boolean
  sub: string
}

/** Longest prerequisite chain per node, from the edges we were given. */
function depths(payload: GraphPayload): Map<string, number> {
  const parents = new Map<string, string[]>()
  payload.nodes.forEach((node) => parents.set(node.id, []))
  payload.edges.forEach((edge) => parents.get(edge.target)?.push(edge.source))

  const memo = new Map<string, number>()
  const visiting = new Set<string>()

  const walk = (id: string): number => {
    const cached = memo.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 0 // defensive: the server guarantees a DAG
    visiting.add(id)
    const value = (parents.get(id) ?? []).reduce((best, p) => Math.max(best, walk(p) + 1), 0)
    visiting.delete(id)
    memo.set(id, value)
    return value
  }

  payload.nodes.forEach((node) => walk(node.id))
  return memo
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

export default function SkillMap({ payload }: { payload: GraphPayload }) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null)

  const { placed, edges, width, height } = useMemo(() => {
    const depth = depths(payload)
    const perColumn = new Map<number, number>()
    const byId = new Map<string, Placed>()

    payload.nodes
      .slice()
      .sort((a, b) => (depth.get(a.id)! - depth.get(b.id)!) || a.id.localeCompare(b.id))
      .forEach((node) => {
        const column = depth.get(node.id) ?? 0
        const row = perColumn.get(column) ?? 0
        perColumn.set(column, row + 1)
        byId.set(node.id, {
          id: node.id,
          name: node.name,
          x: PAD + column * COLUMN,
          y: PAD + row * ROW,
          colour: colourFor(node.mastery, node.in_path, node.is_goal),
          active: node.in_path || node.is_goal,
          isGoal: node.is_goal,
          sub: node.week ? `week ${node.week}` : node.track.replace(/-/g, ' '),
        })
      })

    const lines = payload.edges
      .map((edge, index) => {
        const from = byId.get(edge.source)
        const to = byId.get(edge.target)
        if (!from || !to) return null
        const x1 = from.x + NODE_W
        const y1 = from.y + NODE_H / 2
        const x2 = to.x
        const y2 = to.y + NODE_H / 2
        const bend = Math.max(28, (x2 - x1) / 2)
        return {
          key: `e${index}`,
          d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
          inPath: edge.in_path,
        }
      })
      .filter((line): line is NonNullable<typeof line> => line !== null)

    const nodes = [...byId.values()]
    const maxX = Math.max(...nodes.map((n) => n.x + NODE_W), 400)
    const maxY = Math.max(...nodes.map((n) => n.y + NODE_H), 200)
    return { placed: nodes, edges: lines, width: maxX + PAD, height: maxY + PAD }
  }, [payload])

  return (
    <figure className="m-0">
      <div className="relative overflow-hidden rounded-xl border border-paper-400 bg-paper-200/40">
        <div className="absolute right-3 top-3 z-10 flex gap-1">
          <MapButton label="Zoom in" onClick={() => setZoom((z) => Math.min(2, z + 0.2))}>
            +
          </MapButton>
          <MapButton label="Zoom out" onClick={() => setZoom((z) => Math.max(0.3, z - 0.2))}>
            −
          </MapButton>
          <MapButton
            label="Reset view"
            onClick={() => {
              setZoom(1)
              setPan({ x: 0, y: 0 })
            }}
          >
            ⟲
          </MapButton>
        </div>

        <svg
          role="img"
          aria-label={`Skill map: ${placed.length} skills, ${edges.length} dependencies`}
          viewBox={`0 0 ${width} ${height}`}
          className="h-[440px] w-full cursor-grab touch-none active:cursor-grabbing"
          onWheel={(event) => {
            event.preventDefault()
            setZoom((z) => Math.max(0.3, Math.min(2, z - Math.sign(event.deltaY) * 0.12)))
          }}
          onPointerDown={(event) => {
            drag.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y }
            event.currentTarget.setPointerCapture(event.pointerId)
          }}
          onPointerMove={(event) => {
            if (!drag.current) return
            setPan({
              x: drag.current.panX + (event.clientX - drag.current.x) / zoom,
              y: drag.current.panY + (event.clientY - drag.current.y) / zoom,
            })
          }}
          onPointerUp={() => {
            drag.current = null
          }}
        >
          <defs>
            <pattern id="map-grid" width="24" height="24" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill="#D5C9B4" opacity="0.55" />
            </pattern>
          </defs>
          <rect width={width} height={height} fill="url(#map-grid)" />

          <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {edges.map((edge) => (
              <path
                key={edge.key}
                d={edge.d}
                fill="none"
                stroke={edge.inPath ? MAP_COLOURS.goal : '#CDBFA8'}
                strokeWidth={edge.inPath ? 1.7 : 1}
                opacity={edge.inPath ? 0.85 : 0.45}
              />
            ))}

            {placed.map((node) => (
              <g key={node.id} opacity={node.active ? 1 : 0.6}>
                <title>{`${node.name} — ${node.sub}`}</title>
                <rect
                  x={node.x}
                  y={node.y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={8}
                  fill={node.active ? '#FEFCF8' : '#F2ECE0'}
                  stroke={node.colour}
                  strokeWidth={node.isGoal ? 2.2 : 1.3}
                />
                <text
                  x={node.x + 10}
                  y={node.y + 16}
                  fontSize="10.5"
                  fontWeight="600"
                  fill={node.active ? '#2A251F' : '#8B8175'}
                >
                  {truncate(node.name, 22)}
                </text>
                <text x={node.x + 10} y={node.y + 29} fontSize="9" fill="#A9A093">
                  {truncate(node.sub, 24)}
                </text>
              </g>
            ))}
          </g>
        </svg>
      </div>
      <figcaption className="mt-2 text-[12px] text-ink-400">
        {placed.length} skills, {edges.length} dependencies. Drag to move, scroll to zoom.
      </figcaption>
    </figure>
  )
}

function MapButton({
  children,
  label,
  onClick,
}: {
  children: React.ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-paper-400 bg-paper-50 text-sm text-ink-500 hover:bg-paper-200 hover:text-ink-900"
    >
      {children}
    </button>
  )
}

export function GraphLegend() {
  const swatches = [
    { colour: MAP_COLOURS.goal, label: 'your goal' },
    { colour: MAP_COLOURS.known, label: 'you know this' },
    { colour: MAP_COLOURS.partial, label: 'partly known' },
    { colour: MAP_COLOURS.todo, label: 'in your plan' },
    { colour: MAP_COLOURS.other, label: 'not needed' },
  ]
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-ink-400">
      {swatches.map((swatch) => (
        <span key={swatch.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2.5 w-2.5 rounded-full border-[1.5px]"
            style={{ borderColor: swatch.colour, background: `${swatch.colour}22` }}
          />
          {swatch.label}
        </span>
      ))}
    </div>
  )
}
