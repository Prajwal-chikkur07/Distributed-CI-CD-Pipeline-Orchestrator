export type AgentType = 'build' | 'test' | 'security' | 'deploy' | 'verify';

export type StageStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped';

export interface Stage {
  id: string;
  agent: AgentType;
  command: string;
  depends_on: string[];
  timeout_seconds: number;
  retry_count: number;
  critical: boolean;
  env_vars: Record<string, string>;
}

export interface RepoAnalysis {
  language: string;
  framework: string | null;
  package_manager: string;
  has_dockerfile: boolean;
  has_tests: boolean;
  test_runner: string | null;
  is_monorepo: boolean;
}

export interface PipelineSpec {
  pipeline_id: string;
  name: string;
  repo_url: string;
  goal: string;
  created_at: string;
  analysis: RepoAnalysis;
  stages: Stage[];
}

export interface StageResult {
  stage_id: string;
  status: StageStatus;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_seconds: number;
  artifacts: string[];
  metadata: Record<string, unknown>;
}

export interface StageUpdate {
  stage_id: string;
  status: StageStatus;
  duration_seconds?: number;
  log_tail?: string;
  recovery_strategy?: string;
  recovery_reason?: string;
  modified_command?: string;
  log_type?: LogType;
  log_message?: string;
  deploy_url?: string;
}

export type LogType =
  | 'stage_start'
  | 'stage_success'
  | 'stage_failed'
  | 'stage_skipped'
  | 'retry'
  | 'recovery_start'
  | 'recovery_plan'
  | 'recovery_applying'
  | 'recovery_success'
  | 'recovery_failed'
  | 'rollback_step'
  | 'pipeline_start'
  | 'pipeline_done'
  | 'info';

export interface LogEntry {
  timestamp: string;
  stage_id?: string;
  type: LogType;
  message: string;
  details?: string;
}

export interface HistoryEntry {
  pipeline: PipelineSpec;
  results: Record<string, StageResult> | null;
  completedAt: string;
  overallStatus: 'success' | 'failed' | 'partial';
  logs?: LogEntry[];
}
