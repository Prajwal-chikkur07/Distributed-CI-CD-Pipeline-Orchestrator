import { useMemo, useCallback, useState, useRef, useLayoutEffect } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  type NodeTypes,
  type Viewport,
  type ReactFlowInstance,
  BackgroundVariant,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { usePipelineContext } from '../context/PipelineContext';
import { createNodesAndEdges } from '../utils/dagLayout';
import StageNode from './StageNode';

const nodeTypes: NodeTypes = {
  stageNode: StageNode,
};

/**
 * Compute the initial viewport that centers all nodes in the container.
 */
function computeViewport(
  positions: { x: number; y: number }[],
  containerW: number,
  containerH: number,
): Viewport {
  if (positions.length === 0 || containerW === 0 || containerH === 0) {
    return { x: 0, y: 0, zoom: 1 };
  }

  const nodeW = 200;
  const nodeH = 110;
  const pad = 30;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of positions) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + nodeW);
    maxY = Math.max(maxY, p.y + nodeH);
  }

  const graphW = maxX - minX;
  const graphH = maxY - minY;

  const zoom = Math.max(Math.min((containerW - pad * 2) / graphW, (containerH - pad * 2) / graphH, 1), 0.1);
  const x = (containerW - graphW * zoom) / 2 - minX * zoom;
  const y = (containerH - graphH * zoom) / 2 - minY * zoom;

  return { x, y, zoom };
}

function DAGInner() {
  const { currentPipeline, stageStatuses, selectStage } = usePipelineContext();
  const { fitView } = useReactFlow();

  const { nodes, edges } = useMemo(() => {
    if (!currentPipeline) return { nodes: [], edges: [] };
    return createNodesAndEdges(currentPipeline.stages, stageStatuses);
  }, [currentPipeline, stageStatuses]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: { id: string }) => selectStage(node.id),
    [selectStage],
  );

  if (!currentPipeline) return null;

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={onNodeClick}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false}
      nodesConnectable={false}
      panOnDrag
      zoomOnScroll
      zoomOnPinch
      zoomOnDoubleClick={false}
      panOnScroll={false}
      minZoom={0.1}
      maxZoom={1.5}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#E2E8F0" />
      <MiniMap
        nodeColor="#CBD5E1"
        maskColor="rgba(255,255,255,0.8)"
        className="!bg-white !border !border-gray-200 !rounded-lg !shadow-sm"
      />
      <Controls
        showFitView
        showZoom
        showInteractive={false}
        className="!bg-white !border !border-gray-200 !rounded-lg !shadow-sm"
      />
    </ReactFlow>
  );
}

function DAGContent() {
  const { currentPipeline } = usePipelineContext();
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  // Wait for the container to have actual dimensions before mounting ReactFlow
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const check = () => {
      const { width, height } = el.getBoundingClientRect();
      if (width > 0 && height > 0) {
        setReady(true);
      }
    };

    check();
    if (!ready) {
      const ro = new ResizeObserver(check);
      ro.observe(el);
      return () => ro.disconnect();
    }
  }, [ready]);

  if (!currentPipeline) return null;

  return (
    <div ref={containerRef} style={{ position: 'absolute', inset: 0 }}>
      {ready && (
        <ReactFlowProvider key={currentPipeline.pipeline_id}>
          <DAGInner />
        </ReactFlowProvider>
      )}
    </div>
  );
}

export default function PipelineDAG() {
  const { currentPipeline } = usePipelineContext();
  if (!currentPipeline) return null;

  return (
    <div style={{ flex: '1 1 0%', minHeight: 0, width: '100%', position: 'relative' }}>
      <DAGContent />
    </div>
  );
}
