import { useState } from 'react';
import { X, Terminal, Info, Clock, RotateCcw, AlertTriangle, Zap, ScrollText, ExternalLink } from 'lucide-react';
import { usePipelineContext } from '../context/PipelineContext';
import { statusConfig, agentColors } from '../utils/statusColors';
import type { LogType } from '../types/pipeline';

const recoveryBadgeConfig: Record<string, { bg: string; text: string; border: string }> = {
  FIX_AND_RETRY: { bg: '#ecfdf5', text: '#059669', border: '#a7f3d0' },
  SKIP_STAGE:    { bg: '#fefce8', text: '#ca8a04', border: '#fde68a' },
  ROLLBACK:      { bg: '#fff7ed', text: '#ea580c', border: '#fed7aa' },
  ABORT:         { bg: '#fef2f2', text: '#dc2626', border: '#fecaca' },
};

const logTypeColors: Record<string, { color: string; label: string }> = {
  stage_start: { color: '#3b82f6', label: 'START' },
  stage_success: { color: '#059669', label: 'SUCCESS' },
  stage_failed: { color: '#dc2626', label: 'FAILED' },
  stage_skipped: { color: '#ca8a04', label: 'SKIPPED' },
  retry: { color: '#f59e0b', label: 'RETRY' },
  recovery_start: { color: '#8b5cf6', label: 'HEALING' },
  recovery_plan: { color: '#8b5cf6', label: 'PLAN' },
  recovery_applying: { color: '#d97706', label: 'FIXING' },
  recovery_success: { color: '#059669', label: 'HEALED' },
  recovery_failed: { color: '#dc2626', label: 'HEAL FAIL' },
  rollback_step: { color: '#ea580c', label: 'ROLLBACK' },
  info: { color: '#6b7280', label: 'INFO' },
};

export default function StageDetailPanel() {
  const { currentPipeline, selectedStageId, stageStatuses, stageResults, recoveryPlans, executionLogs, selectStage, deployUrl } =
    usePipelineContext();
  const [tab, setTab] = useState<'output' | 'details' | 'logs'>('output');

  if (!selectedStageId || !currentPipeline) return null;

  const stage = currentPipeline.stages.find((s) => s.id === selectedStageId);
  if (!stage) return null;

  const status = stageStatuses.get(stage.id) ?? 'pending';
  const result = stageResults.get(stage.id);
  const config = statusConfig[status];
  const agentColor = agentColors[stage.agent];
  const recovery = recoveryPlans.get(stage.id);

  return (
    <div className="fixed top-0 right-0 h-full w-[420px] bg-white border-l border-gray-200 shadow-xl z-50 flex flex-col animate-in slide-in-from-right">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between flex-shrink-0">
        <div>
          <h3 className="font-semibold text-gray-900">{stage.id}</h3>
          <div className="flex items-center gap-2 mt-1">
            <span
              className="text-xs font-medium px-2 py-0.5 rounded-full"
              style={{ backgroundColor: agentColor.color + '15', color: agentColor.color }}
            >
              {agentColor.label}
            </span>
            <span
              className="text-xs font-medium px-2 py-0.5 rounded-full"
              style={{ backgroundColor: config.bg, color: config.text, border: `1px solid ${config.border}` }}
            >
              {config.label}
            </span>
            {result && (
              <span className="text-xs text-gray-400 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {result.duration_seconds.toFixed(1)}s
              </span>
            )}
          </div>
        </div>
        <button
          onClick={() => selectStage(null)}
          className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <X className="w-5 h-5 text-gray-400" />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 px-5 flex-shrink-0">
        <button
          onClick={() => setTab('output')}
          className={`flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'output'
              ? 'border-accent text-accent'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          <Terminal className="w-3.5 h-3.5" />
          Output
        </button>
        <button
          onClick={() => setTab('details')}
          className={`flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'details'
              ? 'border-accent text-accent'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          <Info className="w-3.5 h-3.5" />
          Details
        </button>
        <button
          onClick={() => setTab('logs')}
          className={`flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'logs'
              ? 'border-accent text-accent'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          <ScrollText className="w-3.5 h-3.5" />
          Logs
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-5">
        {tab === 'logs' ? (
          <div className="space-y-1">
            {(() => {
              const stageLogs = executionLogs.filter((l) => l.stage_id === stage.id);
              if (stageLogs.length === 0) {
                return (
                  <div className="text-sm text-gray-400 text-center py-8">
                    No log entries for this stage yet
                  </div>
                );
              }
              return stageLogs.map((entry, i) => {
                const cfg = logTypeColors[entry.type] || logTypeColors.info;
                return (
                  <div key={i} className="flex items-start gap-2 py-1.5">
                    <span
                      className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5"
                      style={{ backgroundColor: cfg.color + '15', color: cfg.color }}
                    >
                      {cfg.label}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-gray-700 leading-relaxed">{entry.message}</p>
                      {entry.details && (
                        <pre className="text-[10px] font-mono bg-gray-900 text-gray-300 p-2 rounded mt-1 overflow-x-auto whitespace-pre-wrap max-h-24 overflow-y-auto">
                          {entry.details}
                        </pre>
                      )}
                    </div>
                    <span className="text-[10px] text-gray-300 flex-shrink-0 mt-0.5">
                      {new Date(entry.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                  </div>
                );
              });
            })()}
          </div>
        ) : tab === 'output' ? (
          <div className="space-y-3">
            {result?.stdout ? (
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">
                  stdout
                </label>
                <pre className="bg-gray-900 text-green-400 text-xs font-mono p-4 rounded-lg overflow-x-auto whitespace-pre-wrap max-h-[400px] overflow-y-auto">
                  {result.stdout}
                </pre>
              </div>
            ) : (
              <div className="text-sm text-gray-400 text-center py-8">
                {status === 'pending' ? 'Stage has not run yet' : 'No output captured'}
              </div>
            )}

            {result?.stderr && (
              <div>
                <label className="text-xs font-medium text-red-500 uppercase tracking-wider mb-1.5 block">
                  stderr
                </label>
                <pre className="bg-red-950 text-red-300 text-xs font-mono p-4 rounded-lg overflow-x-auto whitespace-pre-wrap max-h-[300px] overflow-y-auto">
                  {result.stderr}
                </pre>
              </div>
            )}

            {stage.agent === 'deploy' && deployUrl && status === 'success' && (
              <a
                href={deployUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-4 py-3 rounded-lg text-sm font-medium transition-colors"
                style={{ backgroundColor: '#ecfdf5', color: '#059669', border: '1px solid #a7f3d0' }}
              >
                <ExternalLink className="w-4 h-4" />
                Application running at {deployUrl}
              </a>
            )}

            {result && result.exit_code !== 0 && result.exit_code !== -1 && (
              <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">
                <AlertTriangle className="w-4 h-4" />
                Exit code: {result.exit_code}
              </div>
            )}

            {recovery && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border-b border-gray-200">
                  <Zap className="w-3.5 h-3.5 text-gray-500" />
                  <span className="text-xs font-medium text-gray-600 uppercase tracking-wider">Recovery Plan</span>
                </div>
                <div className="p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-xs font-semibold px-2.5 py-1 rounded-full"
                      style={{
                        backgroundColor: (recoveryBadgeConfig[recovery.strategy] ?? recoveryBadgeConfig.ABORT).bg,
                        color: (recoveryBadgeConfig[recovery.strategy] ?? recoveryBadgeConfig.ABORT).text,
                        border: `1px solid ${(recoveryBadgeConfig[recovery.strategy] ?? recoveryBadgeConfig.ABORT).border}`,
                      }}
                    >
                      {recovery.strategy.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <p className="text-sm text-gray-700">{recovery.reason}</p>
                  {recovery.modified_command && (
                    <div>
                      <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1 block">
                        Modified Command
                      </label>
                      <pre className="bg-gray-900 text-yellow-300 text-xs font-mono p-3 rounded-lg overflow-x-auto whitespace-pre-wrap">
                        {recovery.modified_command}
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">
                Command
              </label>
              <pre className="bg-gray-100 text-gray-800 text-sm font-mono p-3 rounded-lg">
                {stage.command}
              </pre>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-gray-50 rounded-lg p-3">
                <div className="text-xs text-gray-500">Timeout</div>
                <div className="text-sm font-medium text-gray-800">{stage.timeout_seconds}s</div>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <div className="text-xs text-gray-500">Retries</div>
                <div className="text-sm font-medium text-gray-800 flex items-center gap-1">
                  <RotateCcw className="w-3 h-3" />
                  {stage.retry_count}
                </div>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <div className="text-xs text-gray-500">Critical</div>
                <div className="text-sm font-medium text-gray-800">
                  {stage.critical ? 'Yes' : 'No'}
                </div>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <div className="text-xs text-gray-500">Agent</div>
                <div className="text-sm font-medium" style={{ color: agentColor.color }}>
                  {agentColor.label}
                </div>
              </div>
            </div>

            {stage.depends_on.length > 0 && (
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">
                  Depends On
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {stage.depends_on.map((dep) => (
                    <span
                      key={dep}
                      className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded-md font-mono"
                    >
                      {dep}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {Object.keys(stage.env_vars).length > 0 && (
              <div>
                <label className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5 block">
                  Environment Variables
                </label>
                <div className="bg-gray-100 rounded-lg p-3 space-y-1">
                  {Object.entries(stage.env_vars).map(([k, v]) => (
                    <div key={k} className="text-xs font-mono">
                      <span className="text-blue-600">{k}</span>
                      <span className="text-gray-400">=</span>
                      <span className="text-gray-700">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
