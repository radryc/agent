import { ShieldAlert, Check, X } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";

const TOOL_LABELS: Record<string, string> = {
  guardian_reconcile: "Reconcile partition",
  guardian_deploy_partition: "Deploy partition",
  workspace_commit: "Commit workspace changes",
  workspace_push: "Push workspace commits",
};

export default function ApprovalCard() {
  const { pendingApprovals, approveMutation } = useChatStore();
  if (pendingApprovals.length === 0) return null;

  return (
    <div className="space-y-2 px-4 py-3">
      {pendingApprovals.map((approval) => (
        <div
          key={approval.id}
          className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-200">
            <ShieldAlert size={16} />
            Mutation approval required
          </div>
          <p className="mt-1 text-xs text-gray-300">
            {TOOL_LABELS[approval.tool] || approval.tool}
          </p>
          <p className="mt-1 text-xs text-gray-500">{approval.rationale}</p>
          <pre className="mt-2 max-h-32 overflow-auto rounded border border-dark-500 bg-dark-900/70 p-2 font-mono text-[10px] text-gray-400">
{JSON.stringify(approval.args, null, 2)}
          </pre>
          {approval.diff && (
            <pre className="mt-2 max-h-48 overflow-auto rounded border border-dark-500 bg-dark-900/70 p-2 font-mono text-[10px] text-gray-300">
{approval.diff}
            </pre>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              className="s-btn s-btn-primary text-xs"
              onClick={() => approveMutation(approval.id, true)}
            >
              <Check size={13} />
              Approve &amp; run
            </button>
            <button
              type="button"
              className="s-btn s-btn-ghost text-xs"
              onClick={() => approveMutation(approval.id, false)}
            >
              <X size={13} />
              Reject
            </button>
            <span className="ml-auto font-mono text-[10px] text-gray-500">
              {approval.id}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
