const workflowConsoleRoot = document.getElementById("workflow-console-root");
const workflowFeed = document.getElementById("workflow-feed");
const workflowReplayHeader = document.getElementById("workflow-replay-header");
const workflowReplayEvents = document.getElementById("workflow-replay-events");
const workflowReplayEmpty = document.getElementById("workflow-replay-empty");
const workflowIdInput = document.getElementById("workflow-id-input");
const compareWorkflowIdInput = document.getElementById("compare-workflow-id-input");
const workflowConfidenceBar = document.getElementById("workflow-confidence-bar");
const workflowConfidenceNote = document.getElementById("workflow-confidence-note");
const workflowAlerts = document.getElementById("workflow-alerts");
const stuckWorkflows = document.getElementById("stuck-workflows");
const toolHealthList = document.getElementById("tool-health-list");
const toolLatencyGrid = document.getElementById("tool-latency-grid");
const lineageList = document.getElementById("lineage-list");
const failureSignatureList = document.getElementById("failure-signature-list");
const workflowAnomalyList = document.getElementById("workflow-anomaly-list");
const incidentTriggerRow = document.getElementById("incident-trigger-row");
const streamStatusLabel = document.getElementById("stream-status-label");
const streamStatusIndicator = document.getElementById("stream-status-indicator");
const incidentTitle = document.getElementById("incident-title");
const incidentSummary = document.getElementById("incident-summary");
const incidentPill = document.getElementById("incident-pill");
const incidentBanner = document.getElementById("incident-banner");
const integrityRadialValue = document.getElementById("integrity-radial-value");
const autonomousResolutionValue = document.getElementById("autonomous-resolution-rate");
const autonomousResolutionBar = document.getElementById("autonomous-resolution-bar");
const analyticsReplayLatency = document.getElementById("analytics-replay-latency");
const analyticsNoShowCount = document.getElementById("analytics-no-show-count");
const analyticsGovernanceCount = document.getElementById("analytics-governance-count");
const analyticsReplayDepth = document.getElementById("analytics-replay-depth");
const analyticsThroughputTrend = document.getElementById("analytics-throughput-trend");
const topologyNodeCount = document.getElementById("topology-node-count");
const runtimeThroughputInline = document.getElementById("runtime-throughput-inline");
const liveDockStream = document.getElementById("live-dock-stream");
const topologyMiniGrid = document.getElementById("topology-mini-grid");
const replayScrubber = document.getElementById("replay-scrubber");
const replayScrubberLabel = document.getElementById("replay-scrubber-label");
const adminQueueDepth = document.getElementById("admin-queue-depth");
const adminActiveJobs = document.getElementById("admin-active-jobs");
const adminFailedJobs = document.getElementById("admin-failed-jobs");
const adminRetryJobs = document.getElementById("admin-retry-jobs");
const adminWorkerList = document.getElementById("admin-worker-list");
const adminIncidentList = document.getElementById("admin-incident-list");
const adminFailedNotifications = document.getElementById("admin-failed-notifications");
const adminNotificationList = document.getElementById("admin-notification-list");
const adminEventTimeline = document.getElementById("admin-event-timeline");
const continuityLinkedCount = document.getElementById("continuity-linked-count");
const continuityList = document.getElementById("continuity-list");
const scheduleConflictList = document.getElementById("schedule-conflict-list");
const WORKFLOW_STREAM_VERSION = "v1";

let latestReplaySteps = [];

function normalizeDecision(decision) {
    return String(decision || "pending");
}

function formatDecision(decision) {
    return normalizeDecision(decision).replaceAll("_", " ");
}

function confidenceBand(confidence) {
    const value = Number(confidence || 0);
    if (value >= 80) {
        return "high";
    }
    if (value >= 70) {
        return "medium";
    }
    return "low";
}

function confidenceNote(confidence) {
    const value = Number(confidence || 0);
    if (value >= 80) {
        return "Stable autonomous range";
    }
    if (value >= 70) {
        return "Watch policy thresholds";
    }
    return "Review-heavy workflow mix";
}

function decisionSeverityClass(decision) {
    return `severity-${normalizeDecision(decision).replaceAll("_", "-")}`;
}

function decisionPillClass(decision) {
    return `queue-pill workflow-pill ${normalizeDecision(decision).replaceAll("_", "-")}`;
}

function statusPillClass(status) {
    return `queue-pill workflow-pill ${String(status || "unknown").replaceAll("_", "-")}`;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function buildConfidenceMarkup(confidence) {
    if (confidence === null || confidence === undefined || confidence === "") {
        return "";
    }
    const value = Number(confidence || 0);
    return `
        <div class="confidence-cluster">
            <div class="confidence-meta">
                <span>Confidence</span>
                <strong>${value}%</strong>
            </div>
            <div class="confidence-meter compact" aria-hidden="true">
                <span class="confidence-fill ${confidenceBand(value)}" style="width: ${value}%"></span>
            </div>
        </div>
    `;
}

function buildReasonsMarkup(reasons, limit = 3) {
    if (!Array.isArray(reasons) || reasons.length === 0) {
        return "";
    }
    return `
        <div class="workflow-reasons-inline">
            ${reasons.slice(0, limit).map((reason) => `<span class="reason-chip">${escapeHtml(reason)}</span>`).join("")}
        </div>
    `;
}

function updateMetric(id, value, suffix = "") {
    const node = document.getElementById(id);
    if (!node) {
        return;
    }
    node.textContent = `${value}${suffix}`;
}

function updateConfidenceMetric(value) {
    updateMetric("workflow-confidence", value, "%");
    if (workflowConfidenceBar) {
        workflowConfidenceBar.style.width = `${value}%`;
        workflowConfidenceBar.className = `confidence-fill ${confidenceBand(value)}`;
    }
    if (workflowConfidenceNote) {
        workflowConfidenceNote.textContent = confidenceNote(value);
    }
}

function updateAutonomousResolution(metrics) {
    const denominator = Number(metrics.autonomous_bookings || 0) + Number(metrics.human_review_queue || 0) + Number(metrics.emergency_escalations || 0);
    const rate = denominator ? Math.round((Number(metrics.autonomous_bookings || 0) / denominator) * 1000) / 10 : 0;
    if (autonomousResolutionValue) {
        autonomousResolutionValue.textContent = `${rate}%`;
    }
    if (autonomousResolutionBar) {
        autonomousResolutionBar.style.width = `${rate}%`;
    }
}

function updatePressureBar(id, value, labelId) {
    const bar = document.getElementById(id);
    if (bar) {
        bar.style.width = `${value}%`;
    }
    const label = document.getElementById(labelId);
    if (label) {
        label.textContent = `${value}%`;
    }
}

function updateWorkflowGraph(decision) {
    const branches = document.querySelectorAll(".workflow-node.branch");
    branches.forEach((node) => {
        node.classList.remove("branch-active", "branch-warning", "branch-critical", "branch-success");
    });
    const normalized = normalizeDecision(decision);
    const active = document.querySelector(`.workflow-node.branch[data-branch="${normalized}"]`);
    if (!active) {
        return;
    }
    active.classList.add("branch-active");
    if (normalized === "emergency_escalation") {
        active.classList.add("branch-critical");
    } else if (normalized === "autonomous_booking") {
        active.classList.add("branch-success");
    } else {
        active.classList.add("branch-warning");
    }
}

function renderIncidentTriggers(triggers = []) {
    if (!incidentTriggerRow) {
        return;
    }
    incidentTriggerRow.innerHTML = "";
    (Array.isArray(triggers) ? triggers : []).slice(0, 3).forEach((trigger) => {
        const chip = document.createElement("span");
        chip.className = "reason-chip";
        chip.textContent = trigger;
        incidentTriggerRow.appendChild(chip);
    });
}

function renderOperationalAlerts(alerts = []) {
    if (!workflowAlerts) {
        return;
    }
    workflowAlerts.innerHTML = "";
    if (!Array.isArray(alerts) || alerts.length === 0) {
        workflowAlerts.innerHTML = `<p class="empty-state">No operational alerts right now.</p>`;
        return;
    }
    alerts.forEach((alert) => {
        const node = document.createElement("div");
        node.className = `ops-alert severity-${String(alert.severity || "info")}`;
        node.innerHTML = `<strong>${escapeHtml(String(alert.severity || "info").toUpperCase())}</strong><p>${escapeHtml(alert.message)}</p>`;
        workflowAlerts.appendChild(node);
    });
}

function renderStuckWorkflows(items = []) {
    if (!stuckWorkflows) {
        return;
    }
    stuckWorkflows.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        stuckWorkflows.innerHTML = `<p class="empty-state">No stalled workflows detected.</p>`;
        return;
    }
    items.forEach((item) => {
        const node = document.createElement("div");
        node.className = `ops-list-item severity-${String(item.severity || "warning")}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.workflow_id)}</strong>
                <span class="${decisionPillClass(item.decision)}">${escapeHtml(formatDecision(item.decision))}</span>
            </div>
            <p>${escapeHtml(item.state)} stalled for ${escapeHtml(item.minutes_stalled)} minutes.</p>
        `;
        stuckWorkflows.appendChild(node);
    });
}

function renderToolHealth(items = []) {
    if (!toolHealthList) {
        return;
    }
    toolHealthList.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        toolHealthList.innerHTML = `<p class="empty-state">No subsystem health data yet.</p>`;
        return;
    }
    items.forEach((item) => {
        const node = document.createElement("div");
        node.className = `ops-list-item status-${String(item.status || "healthy")}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(String(item.name || "").replaceAll("_", " "))}</strong>
                <span class="${statusPillClass(item.status)}">${escapeHtml(item.status)}</span>
            </div>
            <p>${escapeHtml(item.metric_label)}: ${escapeHtml(item.metric_value)}</p>
            <small>${escapeHtml(item.detail)}</small>
        `;
        toolHealthList.appendChild(node);
    });
}

function renderToolLatencyProfiles(items = []) {
    if (!toolLatencyGrid) {
        return;
    }
    toolLatencyGrid.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        toolLatencyGrid.innerHTML = `<p class="empty-state">No tool latency profiles yet.</p>`;
        return;
    }
    items.forEach((item, index) => {
        const node = document.createElement("div");
        node.className = `telemetry-card${index === 0 ? " is-live" : ""}`;
        node.innerHTML = `
            <span class="eyebrow">${escapeHtml(item.tool_name)}</span>
            <strong>P95 ${escapeHtml(item.p95_ms)}ms</strong>
            <p>P50 ${escapeHtml(item.p50_ms)}ms · Max ${escapeHtml(item.max_ms)}ms</p>
        `;
        toolLatencyGrid.appendChild(node);
    });
}

function renderFailureSignatures(items = []) {
    if (!failureSignatureList) {
        return;
    }
    failureSignatureList.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        failureSignatureList.innerHTML = `<p class="empty-state">No deterministic failure signatures detected.</p>`;
        return;
    }
    items.forEach((item, index) => {
        const node = document.createElement("div");
        node.className = `ops-list-item severity-${String(item.severity || "warning")}${index === 0 ? " is-live" : ""}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.signature_id)}</strong>
                <span class="${statusPillClass(item.severity)}">${escapeHtml(item.confidence)}%</span>
            </div>
            <p>${escapeHtml(item.pattern?.detail || "")}</p>
            <small>${escapeHtml(item.pattern?.pattern_key || "")} | observed ${escapeHtml(item.pattern?.observed_value || 0)}</small>
        `;
        failureSignatureList.appendChild(node);
    });
}

function renderWorkflowAnomalies(items = []) {
    if (!workflowAnomalyList) {
        return;
    }
    workflowAnomalyList.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        workflowAnomalyList.innerHTML = `<p class="empty-state">No workflow anomalies detected.</p>`;
        return;
    }
    items.forEach((item, index) => {
        const severity = item.score?.severity === "critical" ? "critical" : "warning";
        const node = document.createElement("div");
        node.className = `ops-list-item severity-${severity}${index === 0 ? " is-live" : ""}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.workflow_id)}</strong>
                <span class="queue-pill workflow-pill ${escapeHtml(item.score?.severity || "watch")}">${escapeHtml(item.score?.score || 0)}</span>
            </div>
            <p>${escapeHtml(item.category)} via ${escapeHtml(item.lineage_marker || "")}</p>
            <small>${escapeHtml(item.evidence?.[0]?.detail || "Deviation detected.")}</small>
        `;
        workflowAnomalyList.appendChild(node);
    });
}

function renderLineageSummaries(items = []) {
    if (!lineageList) {
        return;
    }
    lineageList.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
        lineageList.innerHTML = `<p class="empty-state">No lineage summaries yet.</p>`;
        return;
    }
    items.forEach((item) => {
        const node = document.createElement("div");
        node.className = "ops-list-item lineage-item";
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.workflow_id)}</strong>
                <span class="queue-pill workflow-pill stable">root ${escapeHtml(item.root_event_id)}</span>
            </div>
            <p>${escapeHtml(item.event_count)} events, ${escapeHtml(item.tool_invocation_count)} tool calls, latest event ${escapeHtml(item.latest_event_id)}.</p>
            <small>Correlation ${escapeHtml(item.correlation_id)}${item.last_tool_name ? ` | last tool ${escapeHtml(item.last_tool_name)}` : ""}</small>
        `;
        lineageList.appendChild(node);
    });
}

function renderIncidentCorrelation(correlation) {
    const card = document.getElementById("incident-correlation-card");
    if (!card) {
        return;
    }
    if (!correlation) {
        card.innerHTML = `<p class="empty-state">No incident correlation active.</p>`;
        return;
    }
    card.innerHTML = `
        <div class="ops-list-head">
            <strong>${escapeHtml(correlation.probable_incident_source)}</strong>
            <span class="${statusPillClass(correlation.degradation_severity)}">${escapeHtml(correlation.degradation_severity)}</span>
        </div>
        <p>${escapeHtml(correlation.blast_radius?.affected_workflow_count || 0)} workflows, ${escapeHtml(correlation.blast_radius?.affected_tool_count || 0)} subsystems.</p>
        <small>${escapeHtml((correlation.affected_subsystems || []).join(", "))}</small>
        ${buildReasonsMarkup((correlation.evidence_chain || []).map((item) => `${item.signal}: ${item.detail}`), 4)}
    `;
}

function renderOperationalIntelligence(intelligence = {}) {
    renderOperationalAlerts(intelligence.alerts || []);
    renderStuckWorkflows(intelligence.stuck_workflows || []);
    renderToolHealth(intelligence.tool_health || []);
    renderToolLatencyProfiles(intelligence.tool_latency_profiles || []);
    renderFailureSignatures(intelligence.failure_signatures || []);
    renderWorkflowAnomalies(intelligence.anomalies || []);
    renderLineageSummaries(intelligence.lineage_summaries || []);
    renderIncidentCorrelation(intelligence.incident_correlation);

    const queuePressure = intelligence.queue_pressure || {};
    updatePressureBar("pressure-review-bar", queuePressure.review_pressure_pct ?? 0, "pressure-review-value");
    updatePressureBar("pressure-emergency-bar", queuePressure.emergency_pressure_pct ?? 0, "pressure-emergency-value");
    updatePressureBar("pressure-retry-bar", queuePressure.retry_pressure_pct ?? 0, "pressure-retry-value");
    const unresolved = document.getElementById("pressure-unresolved-note");
    if (unresolved) {
        unresolved.textContent = `${queuePressure.unresolved_workflows ?? 0} unresolved workflows across the current orchestration window.`;
    }
    const pressurePill = document.querySelector("#workflow-pressure-panel .workflow-pill");
    if (pressurePill) {
        pressurePill.className = statusPillClass(queuePressure.pressure_level || "stable");
        pressurePill.textContent = String(queuePressure.pressure_level || "stable");
    }

    const incident = intelligence.incident_state || {};
    if (incidentBanner) {
        incidentBanner.className = `incident-hero level-${String(incident.level || "stable")}`;
    }
    if (incidentTitle) {
        incidentTitle.textContent = incident.title || "Nominal";
    }
    if (incidentSummary) {
        incidentSummary.textContent = incident.summary || "";
    }
    if (incidentPill) {
        incidentPill.className = statusPillClass(incident.level || "stable");
        incidentPill.textContent = String(incident.level || "stable");
    }
    renderIncidentTriggers(incident.triggers || []);

    const recovery = intelligence.recovery_metrics || {};
    updateMetric("recovery-success-rate", recovery.recovery_success_rate ?? 0, "%");
    const sla = intelligence.sla_metrics || {};
    updateMetric("avg-workflow-age", sla.avg_workflow_age_minutes ?? 0, "m");
    updateMetric("avg-review-age", sla.avg_review_age_minutes ?? 0, "m");
    updateMetric("avg-resolution-age", sla.avg_resolution_minutes ?? 0, "m");
}

function renderWorkflowFeed(activityFeed = []) {
    if (!workflowFeed) {
        return;
    }
    workflowFeed.innerHTML = `<span class="eyebrow">Incident-Aware Event Stream</span>`;
    if (!Array.isArray(activityFeed) || activityFeed.length === 0) {
        workflowFeed.innerHTML += `<p class="empty-state">No workflow activity yet.</p>`;
        return;
    }
    activityFeed.forEach((item, index) => {
        const decision = normalizeDecision(item.decision);
        const node = document.createElement("div");
        node.className = `workflow-feed-item ${decisionSeverityClass(decision)}${index === 0 ? " is-live" : ""}`;
        node.innerHTML = `
            <div class="workflow-feed-meta">
                <strong>${escapeHtml(item.workflow_id)}</strong>
                <span>${escapeHtml(item.timestamp)}</span>
            </div>
            <div class="workflow-feed-body">
                <p>${escapeHtml(decision.toUpperCase())} via ${escapeHtml(item.agent)} in ${escapeHtml(item.state)}</p>
                <span class="${decisionPillClass(decision)}">${escapeHtml(formatDecision(decision))}</span>
            </div>
            <div class="workflow-feed-trace">
                <span>trace ${escapeHtml(item.trace_id)}</span>
                <span>root ${escapeHtml(item.root_event_id)}</span>
                <span>cause ${escapeHtml(item.causation_id ?? "-")}</span>
            </div>
            ${buildConfidenceMarkup(item.confidence)}
            ${buildReasonsMarkup(item.reasons)}
        `;
        workflowFeed.appendChild(node);
    });
    renderLiveDockFeed(activityFeed);
}

function renderLiveDockFeed(activityFeed = []) {
    if (!liveDockStream) {
        return;
    }
    liveDockStream.innerHTML = "";
    if (!Array.isArray(activityFeed) || activityFeed.length === 0) {
        liveDockStream.innerHTML = `<div class="dock-empty">No live runtime events yet.</div>`;
        return;
    }
    activityFeed.slice(0, 5).forEach((item, index) => {
        const node = document.createElement("div");
        node.className = `dock-stream-item ${decisionSeverityClass(item.decision)}${index === 0 ? " is-live" : ""}`;
        node.innerHTML = `
            <div class="dock-stream-meta">
                <strong>${escapeHtml(item.workflow_id)}</strong>
                <span>${escapeHtml(item.timestamp)}</span>
            </div>
            <p>${escapeHtml(item.agent)} -> ${escapeHtml(item.state)}</p>
            <small>trace ${escapeHtml(item.trace_id)} · root ${escapeHtml(item.root_event_id)}</small>
        `;
        liveDockStream.appendChild(node);
    });
}

function renderWorkflowReplay(replay) {
    if (!workflowReplayHeader || !workflowReplayEvents) {
        return;
    }
    if (!replay || !Array.isArray(replay.steps) || replay.steps.length === 0) {
        latestReplaySteps = [];
        workflowReplayHeader.classList.add("hidden");
        workflowReplayEvents.classList.add("hidden");
        workflowReplayEvents.innerHTML = "";
        workflowReplayEmpty?.classList.remove("hidden");
        updateWorkflowGraph("");
        if (replayScrubber) {
            replayScrubber.value = "0";
            replayScrubber.max = "0";
        }
        if (replayScrubberLabel) {
            replayScrubberLabel.textContent = "No replay loaded";
        }
        return;
    }
    latestReplaySteps = replay.steps.slice();
    workflowReplayHeader.classList.remove("hidden");
    workflowReplayEvents.classList.remove("hidden");
    workflowReplayEmpty?.classList.add("hidden");

    const latestDecision = normalizeDecision(replay.latest_decision);
    workflowReplayHeader.innerHTML = `
        <div>
            <strong>${escapeHtml(replay.workflow_id)}</strong>
            <span>Latest route: ${escapeHtml(formatDecision(latestDecision))}</span>
        </div>
        <span class="${decisionPillClass(latestDecision)}">${escapeHtml(formatDecision(latestDecision))}</span>
    `;
    workflowReplayEvents.innerHTML = "";
    replay.steps.forEach((step, index) => {
        const decision = normalizeDecision(step.decision);
        const node = document.createElement("div");
        node.className = `workflow-event-item ${decisionSeverityClass(decision)}${index === replay.steps.length - 1 ? " is-live" : ""}`;
        node.innerHTML = `
            <div class="workflow-event-meta">
                <span>${escapeHtml(step.state)}</span>
                <span>${escapeHtml(step.agent)}</span>
            </div>
            <div class="workflow-event-head">
                <strong>${escapeHtml(step.action)}</strong>
                <span class="${decisionPillClass(decision)}">${escapeHtml(formatDecision(decision))}</span>
            </div>
            <div class="workflow-event-lineage">
                <span>event ${escapeHtml(step.event_id)}</span>
                <span>cause ${escapeHtml(step.causation_id ?? "-")}</span>
                <span>root ${escapeHtml(step.root_event_id ?? step.event_id)}</span>
                <span>branch ${escapeHtml(step.replay_branch_id || "main")}</span>
            </div>
            <p>${escapeHtml(step.timestamp)}</p>
            ${buildConfidenceMarkup(step.confidence)}
            ${buildReasonsMarkup(step.reasons, 4)}
        `;
        workflowReplayEvents.appendChild(node);
    });
    syncReplayScrubber(replay.steps.length);
    updateWorkflowGraph(latestDecision);
}

function syncReplayScrubber(stepCount) {
    if (!replayScrubber) {
        return;
    }
    replayScrubber.max = String(Math.max(stepCount - 1, 0));
    replayScrubber.value = String(Math.max(stepCount - 1, 0));
    updateReplayScrubberState();
}

function updateReplayScrubberState() {
    if (!replayScrubber || !workflowReplayEvents) {
        return;
    }
    const currentIndex = Number(replayScrubber.value || 0);
    const items = Array.from(workflowReplayEvents.children);
    items.forEach((item, index) => {
        item.classList.toggle("is-dimmed", index > currentIndex);
        item.classList.toggle("is-selected", index === currentIndex);
    });
    if (replayScrubberLabel) {
        const step = latestReplaySteps[currentIndex];
        replayScrubberLabel.textContent = step
            ? `Step ${currentIndex + 1} of ${latestReplaySteps.length}: ${step.agent} -> ${step.state}`
            : "Replay scrubber";
    }
}

function renderReplayIntegrity(integrity) {
    const panel = document.getElementById("workflow-diff-panel");
    if (!panel) {
        return;
    }
    panel.querySelector("#replay-integrity-card")?.remove();
    if (integrityRadialValue) {
        integrityRadialValue.textContent = `${Number(integrity?.integrity_confidence || 0)}%`;
    }
    if (!integrity) {
        return;
    }
    const node = document.createElement("div");
    node.className = `integrity-banner severity-${integrity.replay_match ? "success" : "critical"}`;
    node.id = "replay-integrity-card";
    node.innerHTML = `
        <div>
            <strong>Replay integrity ${escapeHtml(integrity.integrity_confidence)}%</strong>
            <p>Checksum ${escapeHtml(String(integrity.checksum?.value || "").slice(0, 12))}... with ${escapeHtml(integrity.replay_event_count || 0)} canonical replay events.</p>
        </div>
        <div class="integrity-pill-group">
            <span class="queue-pill workflow-pill ${integrity.lineage_consistency ? "stable" : "critical"}">lineage ${escapeHtml(integrity.lineage_consistency)}</span>
            <span class="queue-pill workflow-pill ${integrity.policy_consistency ? "stable" : "critical"}">policy ${escapeHtml(integrity.policy_consistency)}</span>
            <span class="queue-pill workflow-pill ${integrity.retry_consistency ? "stable" : "critical"}">retry ${escapeHtml(integrity.retry_consistency)}</span>
        </div>
    `;
    const eyebrow = panel.querySelector(".eyebrow");
    if (eyebrow?.nextSibling) {
        panel.insertBefore(node, eyebrow.nextSibling);
    } else {
        panel.appendChild(node);
    }
}

function renderWorkflowDiff(diff) {
    const panel = document.getElementById("workflow-diff-panel");
    if (!panel) {
        return;
    }
    panel.querySelector(".diff-workspace")?.remove();
    panel.querySelector("#workflow-diff-empty")?.remove();
    if (!diff) {
        panel.insertAdjacentHTML("beforeend", `<p class="empty-state" id="workflow-diff-empty">Provide two workflow IDs to compare divergence, retries, policy paths, and probable cause.</p>`);
        return;
    }

    const workspace = document.createElement("div");
    workspace.className = "diff-workspace";
    const evidence = Array.isArray(diff.root_cause?.supporting_events)
        ? buildReasonsMarkup(diff.root_cause.supporting_events.map((item) => `${item.signal}: ${item.detail}`), 4)
        : "";
    workspace.innerHTML = `
        <div class="diff-summary-card" id="workflow-diff-header">
            <div>
                <strong>${escapeHtml(diff.workflow_a)} vs ${escapeHtml(diff.workflow_b)}</strong>
                <span>${escapeHtml(diff.summary || "Deterministic replay comparison")}</span>
            </div>
            <span class="queue-pill workflow-pill ${diff.divergence_point !== null && diff.divergence_point !== undefined ? "warning" : "stable"}">${diff.divergence_point !== null && diff.divergence_point !== undefined ? "diverged" : "matched"}</span>
        </div>
        <div class="diff-grid">
            <div class="ops-mini-card"><span class="eyebrow">First divergence</span><strong>${escapeHtml(diff.divergence_point ?? "none")}</strong><p>Execution branch split</p></div>
            <div class="ops-mini-card"><span class="eyebrow">Policy paths</span><strong>${escapeHtml(diff.policy_path_delta || "matched")}</strong><p>Deterministic policy comparison</p></div>
            <div class="ops-mini-card"><span class="eyebrow">Retry delta</span><strong>${escapeHtml(diff.retry_delta || 0)}</strong><p>Recovery chain delta</p></div>
            <div class="ops-mini-card"><span class="eyebrow">Latency delta</span><strong>${escapeHtml(diff.latency_delta_ms || 0)}ms</strong><p>Execution timing variance</p></div>
        </div>
        ${diff.root_cause ? `
            <div class="root-cause-card severity-warning" id="workflow-root-cause">
                <div class="ops-list-head">
                    <strong>Probable cause</strong>
                    <span class="queue-pill workflow-pill warning">${escapeHtml(diff.root_cause.confidence)}%</span>
                </div>
                <p>${escapeHtml(diff.root_cause.probable_cause)}</p>
                <small>${escapeHtml(diff.root_cause.retry_correlation || "")} ${escapeHtml(diff.root_cause.incident_correlation || "")}</small>
                ${evidence}
            </div>
        ` : ""}
        <div class="diff-compare-grid">
            <div class="diff-column">
                <span class="eyebrow">Workflow A</span>
                <div class="ops-list">
                    ${(diff.differing_events || []).map((item) => `
                        <div class="ops-list-item severity-warning">
                            <div class="ops-list-head">
                                <strong>Step ${escapeHtml((item.event_index ?? 0) + 1)}</strong>
                                <span class="queue-pill workflow-pill warning">${escapeHtml(item.workflow_a_decision)}</span>
                            </div>
                            <p>${escapeHtml(item.workflow_a_action)}</p>
                            <small>event ${escapeHtml(item.workflow_a_event_id)}</small>
                        </div>
                    `).join("")}
                </div>
            </div>
            <div class="diff-column">
                <span class="eyebrow">Workflow B</span>
                <div class="ops-list" id="workflow-diff-events">
                    ${(diff.differing_events || []).map((item) => `
                        <div class="ops-list-item severity-critical">
                            <div class="ops-list-head">
                                <strong>Step ${escapeHtml((item.event_index ?? 0) + 1)}</strong>
                                <span class="queue-pill workflow-pill critical">${escapeHtml(item.workflow_b_decision)}</span>
                            </div>
                            <p>${escapeHtml(item.workflow_b_action)}</p>
                            <small>${escapeHtml(item.summary)}</small>
                        </div>
                    `).join("")}
                </div>
            </div>
        </div>
    `;
    panel.appendChild(workspace);
}

function setStreamStatus(level, label) {
    if (streamStatusLabel) {
        streamStatusLabel.textContent = label;
    }
    if (streamStatusIndicator) {
        streamStatusIndicator.className = `status-chip status-${level}`;
    }
}

function renderOperationalAnalytics(analytics) {
    if (!analytics || typeof analytics !== "object") {
        return;
    }
    if (analyticsReplayLatency) {
        analyticsReplayLatency.textContent = `${Number(analytics.replay_latency_ms || 0).toFixed(1)}ms`;
    }
    if (analyticsNoShowCount) {
        analyticsNoShowCount.textContent = String(analytics.no_show_count || 0);
    }
    if (analyticsGovernanceCount) {
        analyticsGovernanceCount.textContent = String(analytics.governance_review_count || 0);
    }
    if (analyticsReplayDepth) {
        analyticsReplayDepth.textContent = String(analytics.average_replay_depth || 0);
    }
    if (!analyticsThroughputTrend) {
        if (runtimeThroughputInline) {
            runtimeThroughputInline.textContent = String(
                (Array.isArray(analytics.workflow_throughput) ? analytics.workflow_throughput : []).reduce(
                    (total, item) => total + Number(item.value || 0),
                    0,
                ),
            );
        }
        return;
    }
    analyticsThroughputTrend.innerHTML = "";
    const items = Array.isArray(analytics.workflow_throughput) ? analytics.workflow_throughput : [];
    const maxValue = Math.max(...items.map((item) => Number(item.value || 0)), 1);
    const total = items.reduce((sum, item) => sum + Number(item.value || 0), 0);
    if (runtimeThroughputInline) {
        runtimeThroughputInline.textContent = String(total);
    }
    items.forEach((item) => {
        const node = document.createElement("div");
        node.className = "trend-bar";
        const height = Math.max(12, Math.round((Number(item.value || 0) / maxValue) * 100));
        node.innerHTML = `
            <span class="trend-bar-value">${escapeHtml(item.value)}</span>
            <div class="trend-bar-fill" style="height:${height}px"></div>
            <span class="trend-bar-label">${escapeHtml(item.label)}</span>
        `;
        analyticsThroughputTrend.appendChild(node);
    });
}

function renderTopology(topology = {}) {
    const nodes = Array.isArray(topology.runtime_nodes) ? topology.runtime_nodes : [];
    if (topologyNodeCount) {
        topologyNodeCount.textContent = String(nodes.length);
    }
    if (!topologyMiniGrid) {
        return;
    }
    topologyMiniGrid.innerHTML = "";
    if (nodes.length === 0) {
        topologyMiniGrid.innerHTML = `<div class="dock-empty">No runtime nodes reported yet.</div>`;
        return;
    }
    nodes.slice(0, 6).forEach((node) => {
        const card = document.createElement("div");
        card.className = "topology-mini-card";
        card.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(node.node_id)}</strong>
                <span class="${statusPillClass(node.status)}">${escapeHtml(node.status)}</span>
            </div>
            <p>w${escapeHtml(node.worker_generation)} · s${escapeHtml(node.stream_generation)} · r${escapeHtml(node.replay_generation)}</p>
            <small>${escapeHtml(node.heartbeat_at)}</small>
        `;
        topologyMiniGrid.appendChild(card);
    });
}

function renderAdminQueues(payload = {}) {
    const queue = payload.queue || {};
    if (adminQueueDepth) {
        adminQueueDepth.textContent = String(queue.queue_depth ?? 0);
    }
    if (adminActiveJobs) {
        adminActiveJobs.textContent = String(queue.active_jobs ?? 0);
    }
    if (adminFailedJobs) {
        adminFailedJobs.textContent = String(queue.failed_jobs ?? 0);
    }
    if (adminRetryJobs) {
        adminRetryJobs.textContent = String(queue.retry_jobs ?? 0);
    }
}

function renderAdminWorkers(payload = {}) {
    if (!adminWorkerList) {
        return;
    }
    const workers = Array.isArray(payload.workers?.workers) ? payload.workers.workers : [];
    adminWorkerList.innerHTML = "";
    if (workers.length === 0) {
        adminWorkerList.innerHTML = `<p class="empty-state">No worker telemetry yet.</p>`;
        return;
    }
    workers.slice(0, 6).forEach((worker) => {
        const states = Object.entries(worker.states || {}).map(([key, value]) => `${key}:${value}`).join(" · ");
        const node = document.createElement("div");
        node.className = `ops-list-item ${worker.stale ? "severity-warning" : "status-healthy"}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(worker.worker_id)}</strong>
                <span class="${statusPillClass(worker.stale ? "warning" : "stable")}">${worker.stale ? "stale" : "healthy"}</span>
            </div>
            <p>${escapeHtml(states || "No state counts")}</p>
            <small>${escapeHtml(worker.heartbeat || worker.latest_update || "No heartbeat yet")}</small>
        `;
        adminWorkerList.appendChild(node);
    });
}

function renderAdminIncidents(payload = {}) {
    if (adminIncidentList) {
        adminIncidentList.innerHTML = "";
        const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
        if (alerts.length === 0) {
            adminIncidentList.innerHTML = `<p class="empty-state">No incident alerts active.</p>`;
        } else {
            alerts.slice(0, 6).forEach((alert) => {
                const node = document.createElement("div");
                node.className = `ops-alert severity-${escapeHtml(alert.severity || "info")}`;
                node.innerHTML = `<strong>${escapeHtml(String(alert.severity || "info").toUpperCase())}</strong><p>${escapeHtml(alert.message)}</p>`;
                adminIncidentList.appendChild(node);
            });
        }
    }
    if (adminFailedNotifications) {
        adminFailedNotifications.innerHTML = "";
        const failed = Array.isArray(payload.failed_notifications) ? payload.failed_notifications : [];
        if (failed.length === 0) {
            adminFailedNotifications.innerHTML = `<p class="empty-state">No failed notifications currently open.</p>`;
        } else {
            failed.slice(0, 6).forEach((item) => {
                const node = document.createElement("div");
                node.className = "ops-list-item severity-critical";
                node.innerHTML = `
                    <div class="ops-list-head">
                        <strong>${escapeHtml(item.target_name)}</strong>
                        <span class="queue-pill workflow-pill critical">${escapeHtml(item.channel)}</span>
                    </div>
                    <p>${escapeHtml(item.failure_reason || item.status)}</p>
                    <small>notification ${escapeHtml(item.id)} · ${escapeHtml(item.created_at)}</small>
                `;
                adminFailedNotifications.appendChild(node);
            });
        }
    }
}

function renderAdminNotifications(payload = {}) {
    if (!adminNotificationList) {
        return;
    }
    const items = Array.isArray(payload.items) ? payload.items : [];
    adminNotificationList.innerHTML = "";
    if (items.length === 0) {
        adminNotificationList.innerHTML = `<p class="empty-state">No delivery telemetry yet.</p>`;
        return;
    }
    items.slice(0, 8).forEach((item) => {
        const node = document.createElement("div");
        node.className = "notification-item";
        node.innerHTML = `
            <div>
                <span class="notification-target">${escapeHtml(item.target_type)} -> ${escapeHtml(item.target_name)}</span>
                <p>${escapeHtml(item.message)}</p>
                <small>${escapeHtml(item.twilio_sid || "pending provider id")}</small>
            </div>
            <div class="notification-meta"><strong>${escapeHtml(item.channel)}</strong><span>${escapeHtml(item.status)}</span></div>
        `;
        adminNotificationList.appendChild(node);
    });
}

function renderAdminEventTimeline(payload = {}) {
    if (!adminEventTimeline) {
        return;
    }
    const items = Array.isArray(payload.items) ? payload.items : [];
    adminEventTimeline.innerHTML = "";
    if (items.length === 0) {
        adminEventTimeline.innerHTML = `<p class="empty-state">No canonical operational events yet.</p>`;
        return;
    }
    items.slice(0, 10).forEach((item) => {
        const node = document.createElement("div");
        node.className = `ops-list-item severity-${escapeHtml(item.severity || "info")}`;
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.canonical_event)}</strong>
                <span class="${decisionPillClass(item.decision)}">${escapeHtml(formatDecision(item.decision))}</span>
            </div>
            <p>${escapeHtml(item.workflow_id)} · ${escapeHtml(item.action)}</p>
            <small>${escapeHtml(item.timestamp)} · ${escapeHtml(item.agent)}</small>
        `;
        adminEventTimeline.appendChild(node);
    });
}

function renderPatientContinuity(payload = {}) {
    if (continuityLinkedCount) {
        continuityLinkedCount.textContent = String(payload.linked_patient_count ?? 0);
    }
    if (!continuityList) {
        return;
    }
    const items = Array.isArray(payload.items) ? payload.items : [];
    continuityList.innerHTML = "";
    if (items.length === 0) {
        continuityList.innerHTML = `<p class="empty-state">No linked patient profiles yet.</p>`;
        return;
    }
    items.slice(0, 8).forEach((item) => {
        const node = document.createElement("div");
        node.className = "ops-list-item";
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.patient_name)}</strong>
                <span class="${statusPillClass(item.whatsapp_enabled ? "stable" : "watch")}">${item.whatsapp_enabled ? "whatsapp" : "profile only"}</span>
            </div>
            <p>${escapeHtml(item.patient_email || item.phone)}</p>
            <small>linked user ${escapeHtml(item.linked_user_id)} · updated ${escapeHtml(item.updated_at)}</small>
        `;
        continuityList.appendChild(node);
    });
}

function renderScheduleGovernance(payload = {}) {
    if (!scheduleConflictList) {
        return;
    }
    const conflicts = Array.isArray(payload.conflicts) ? payload.conflicts : [];
    scheduleConflictList.innerHTML = "";
    if (conflicts.length === 0) {
        scheduleConflictList.innerHTML = `<p class="empty-state">No slot conflicts detected.</p>`;
        return;
    }
    conflicts.slice(0, 8).forEach((item) => {
        const node = document.createElement("div");
        node.className = "ops-list-item severity-warning";
        node.innerHTML = `
            <div class="ops-list-head">
                <strong>${escapeHtml(item.doctor_name)}</strong>
                <span class="queue-pill workflow-pill warning">${escapeHtml(item.count)} bookings</span>
            </div>
            <p>${escapeHtml(item.appointment_date)} at ${escapeHtml(item.slot_time)}</p>
        `;
        scheduleConflictList.appendChild(node);
    });
}

async function loadTopology() {
    if (!workflowConsoleRoot) {
        return;
    }
    try {
        const response = await fetch("/api/observability/topology");
        if (!response.ok) {
            return;
        }
        renderTopology(await response.json());
    } catch (error) {
        console.warn("topology fetch unavailable", error);
    }
}

function bindWorkspaceControls() {
    const densityToggle = document.querySelector("[data-density-toggle]");
    const focusToggle = document.querySelector("[data-focus-toggle]");
    const root = workflowConsoleRoot;
    if (!root) {
        return;
    }
    densityToggle?.addEventListener("click", () => {
        root.classList.toggle("compact-density");
    });
    focusToggle?.addEventListener("click", () => {
        root.classList.toggle("focus-mode");
    });
    replayScrubber?.addEventListener("input", updateReplayScrubberState);
}

async function loadOperationalAnalytics() {
    if (!workflowConsoleRoot) {
        return;
    }
    const tenantKey = workflowConsoleRoot.dataset.tenantKey || "default-clinic";
    try {
        const response = await fetch(`/api/analytics/operational?tenant_key=${encodeURIComponent(tenantKey)}`);
        if (!response.ok) {
            return;
        }
        renderOperationalAnalytics(await response.json());
    } catch (error) {
        console.warn("analytics fetch unavailable", error);
    }
}

async function loadAdminCommandCenter() {
    if (!workflowConsoleRoot) {
        return;
    }
    try {
        const [queuesResponse, workersResponse, incidentsResponse, notificationsResponse, eventsResponse, continuityResponse, scheduleResponse] = await Promise.all([
            fetch("/admin/runtime/queues"),
            fetch("/admin/runtime/workers"),
            fetch("/admin/incidents"),
            fetch("/admin/notifications?page_size=8"),
            fetch("/admin/events?page_size=10"),
            fetch("/admin/continuity?limit=8"),
            fetch("/admin/schedules?limit=16"),
        ]);
        if (queuesResponse?.ok) {
            renderAdminQueues(await queuesResponse.json());
        }
        if (workersResponse?.ok) {
            renderAdminWorkers(await workersResponse.json());
        }
        if (incidentsResponse?.ok) {
            renderAdminIncidents(await incidentsResponse.json());
        }
        if (notificationsResponse?.ok) {
            renderAdminNotifications(await notificationsResponse.json());
        }
        if (eventsResponse?.ok) {
            renderAdminEventTimeline(await eventsResponse.json());
        }
        if (continuityResponse?.ok) {
            renderPatientContinuity(await continuityResponse.json());
        }
        if (scheduleResponse?.ok) {
            renderScheduleGovernance(await scheduleResponse.json());
        }
    } catch (error) {
        console.warn("admin command center fetch unavailable", error);
    }
}

function connectWorkflowStream() {
    if (!workflowConsoleRoot || typeof EventSource === "undefined") {
        return;
    }
    const workflowId = workflowConsoleRoot.dataset.workflowId || workflowIdInput?.value || "";
    const compareWorkflowId = workflowConsoleRoot.dataset.compareWorkflowId || compareWorkflowIdInput?.value || "";
    setStreamStatus("watch", "SSE connecting");
    const source = new EventSource(`/api/workflows/stream?workflow_id=${encodeURIComponent(workflowId)}&compare_workflow_id=${encodeURIComponent(compareWorkflowId)}`);

    source.addEventListener("open", () => {
        setStreamStatus("healthy", "SSE live");
    });

    source.addEventListener("error", () => {
        setStreamStatus("degraded", "SSE degraded");
    });

    source.addEventListener("workflow", (event) => {
        const payload = JSON.parse(event.data || "{}");
        if (payload.version !== WORKFLOW_STREAM_VERSION || typeof payload.workflow_metrics !== "object") {
            return;
        }
        setStreamStatus("healthy", "SSE live");
        const metrics = payload.workflow_metrics || {};
        updateMetric("workflow-active-count", metrics.active_workflows ?? 0);
        updateMetric("workflow-review-count", metrics.human_review_queue ?? 0);
        updateMetric("workflow-emergency-count", metrics.emergency_escalations ?? 0);
        updateMetric("workflow-autonomous-count", metrics.autonomous_bookings ?? 0);
        updateMetric("workflow-failed-count", metrics.failed_recoveries ?? 0);
        updateConfidenceMetric(metrics.average_confidence ?? 0);
        updateAutonomousResolution(metrics);
        renderWorkflowFeed(metrics.activity_feed || []);
        renderOperationalIntelligence(payload.operational_intelligence || {});
        renderWorkflowReplay(payload.workflow_replay);
        renderReplayIntegrity(payload.replay_integrity);
        renderWorkflowDiff(payload.workflow_diff);
    });
}

connectWorkflowStream();
loadOperationalAnalytics();
loadTopology();
loadAdminCommandCenter();
bindWorkspaceControls();
if (workflowConsoleRoot) {
    window.setInterval(loadAdminCommandCenter, 10000);
}
