/**
 * The skill DAG, coloured by mastery and laid out by dependency depth.
 *
 * Layout is computed here rather than by a force simulation, because the whole
 * claim of the product is that the graph has a *direction*: depth = longest
 * prerequisite chain, so x position literally means "how far into the
 * dependency order this sits". A physics layout would scramble the one property
 * the visualisation exists to show.
 *
 * Performance: nodes and edges are memoised and ReactFlow runs with
 * `nodesDraggable={false}`, so 150 nodes render without interaction jank.
 */

import { useMemo } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Edge,
  type Node,
  Position,
} from 'reactflow'
import 'reactflow/dist/style.css'
import type { GraphPayload } from '../lib/api'

const COLUMN_WIDTH = 210
const ROW_HEIGHT = 62

function masteryColour(mastery: number, inPath: boolean, isGoal: boolean): string {
  if (isGoal) return '#f59331'
  if (mastery >= 0.7) return '#4ade80'
  if (mastery > 0) return '#fbbf24'
  return inPath ? '#37497a' : '#1b2748'
}

/** Longest prerequisite chain per node, computed from the edges we were given. */
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
    const value = (parents.get(id) ?? []).reduce((best, parent) => Math.max(best, walk(parent) + 1), 0)
    visiting.delete(id)
    memo.set(id, value)
    return value
  }

  payload.nodes.forEach((node) => walk(node.id))
  return memo
}

export default function SkillGraphView({ payload }: { payload: GraphPayload }) {
  const { nodes, edges } = useMemo(() => {
    const depth = depths(payload)
    const perColumn = new Map<number, number>()

    const laidOut: Node[] = payload.nodes
      .slice()
      .sort((a, b) => (depth.get(a.id)! - depth.get(b.id)!) || a.id.localeCompare(b.id))
      .map((node) => {
        const column = depth.get(node.id) ?? 0
        const row = perColumn.get(column) ?? 0
        perColumn.set(column, row + 1)
        const colour = masteryColour(node.mastery, node.in_path, node.is_goal)

        return {
          id: node.id,
          position: { x: column * COLUMN_WIDTH, y: row * ROW_HEIGHT },
          data: {
            label: (
              <div className="max-w-[170px] text-left leading-tight">
                <div className="truncate text-[11px] font-semibold">{node.name}</div>
                <div className="truncate text-[10px] opacity-70">
                  {node.week ? `week ${node.week}` : node.track}
                </div>
              </div>
            ),
          },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          draggable: false,
          style: {
            width: 178,
            padding: '6px 9px',
            borderRadius: 9,
            border: `1.5px solid ${colour}`,
            background: node.in_path ? 'rgba(18,28,55,0.96)' : 'rgba(10,16,34,0.72)',
            color: node.in_path ? '#e8ecf7' : '#7784a6',
            opacity: node.in_path || node.is_goal ? 1 : 0.55,
            fontSize: 11,
          },
        }
      })

    const drawn: Edge[] = payload.edges.map((edge) => ({
      id: `${edge.source}->${edge.target}`,
      source: edge.source,
      target: edge.target,
      animated: false,
      style: {
        stroke: edge.in_path ? '#f59331' : '#26355e',
        strokeWidth: edge.in_path ? 1.6 : 1,
        opacity: edge.in_path ? 0.9 : 0.4,
      },
    }))

    return { nodes: laidOut, edges: drawn }
  }, [payload])

  return (
    <div className="h-[520px] w-full overflow-hidden rounded-xl border border-ink-700 bg-ink-950/50">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        minZoom={0.08}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1b2748" gap={22} />
        <Controls showInteractive={false} className="!bg-ink-800 !text-mist-300" />
        <MiniMap
          pannable
          zoomable
          className="!hidden sm:!block !bg-ink-900"
          maskColor="rgba(6,10,22,0.75)"
          nodeColor={(node) => (node.style?.border as string)?.split(' ').pop() ?? '#26355e'}
        />
      </ReactFlow>
    </div>
  )
}

export function GraphLegend() {
  const swatches = [
    { colour: '#f59331', label: 'your goal' },
    { colour: '#4ade80', label: 'mastered' },
    { colour: '#fbbf24', label: 'partly known' },
    { colour: '#37497a', label: 'to learn' },
    { colour: '#1b2748', label: 'not required' },
  ]
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-mist-500">
      {swatches.map((swatch) => (
        <span key={swatch.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2.5 w-2.5 rounded-full border"
            style={{ borderColor: swatch.colour, background: `${swatch.colour}33` }}
          />
          {swatch.label}
        </span>
      ))}
    </div>
  )
}
