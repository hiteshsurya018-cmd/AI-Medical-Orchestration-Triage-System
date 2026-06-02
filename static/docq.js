const intakeForm = document.getElementById("intake-form");
const symptomsField = document.getElementById("symptoms");
const chatStream = document.getElementById("chat-stream");
const workflowStepper = document.getElementById("workflow-stepper");
const drawer = document.getElementById("context-drawer");
const closeDrawerButton = document.getElementById("close-drawer");
const drawerStageTitle = document.getElementById("drawer-stage-title");
const sidebar = document.getElementById("patient-sidebar");
const sidebarToggle = document.getElementById("sidebar-toggle");
const chatCharCounter = document.getElementById("chat-char-counter");
const summaryStrip = document.getElementById("workspace-summary-strip");
const summaryReadinessCopy = document.getElementById("summary-readiness-copy");
const openBookingModalButton = document.getElementById("open-booking-modal");
const bookingModal = document.getElementById("booking-modal");
const closeBookingModalButton = document.getElementById("close-booking-modal");
const bookingForm = document.getElementById("booking-form");
const bookingDate = document.getElementById("booking-date");
const bookingAge = document.getElementById("booking-age");
const bookingSpecialty = document.getElementById("booking-specialty");
const bookingDoctorName = document.getElementById("booking-doctor-name");
const bookingDoctorCards = document.getElementById("booking-doctor-cards");
const bookingDoctorHint = document.getElementById("booking-doctor-hint");
const bookingSymptoms = document.getElementById("booking-symptoms");
const bookingSubmit = document.getElementById("booking-submit");
const bookingFeedback = document.getElementById("booking-feedback");
const automationSection = document.getElementById("drawer-automation");
const automationStageName = document.getElementById("automation-stage-name");
const automationStageCopy = document.getElementById("automation-stage-copy");
const memorySection = document.getElementById("drawer-memory");
const memoryConditions = document.getElementById("drawer-memory-conditions");
const memoryLastVisit = document.getElementById("drawer-memory-last-visit");
const memoryTimeline = document.getElementById("drawer-memory-timeline");
const careTeamSection = document.getElementById("drawer-care-team");
const careTeamGrid = document.getElementById("drawer-care-team-grid");
const quickAidSection = document.getElementById("drawer-quick-aid");
const quickAidBody = document.getElementById("drawer-quick-aid-body");
const workspaceProfile = document.getElementById("workspace-profile");
const workspaceSession = document.getElementById("workspace-session");
const openSignupModalButton = document.getElementById("open-signup-modal");
const signupModal = document.getElementById("signup-modal");
const closeSignupModalButton = document.getElementById("close-signup-modal");
const signupForm = document.getElementById("signup-form");
const signupFeedback = document.getElementById("signup-feedback");
const communicationTimelineStrip = document.getElementById("communication-timeline-strip");
const communicationPreferenceCopy = document.getElementById("communication-preference-copy");
const openProfileModalButton = document.getElementById("open-profile-modal");
const profileModal = document.getElementById("profile-modal");
const closeProfileModalButton = document.getElementById("close-profile-modal");
const profileForm = document.getElementById("profile-form");
const profileFeedback = document.getElementById("profile-feedback");
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

let latestIntake = null;
let drawerVisible = false;

const workflowStageOrder = ["memory", "intake", "followup", "triage", "decision", "scheduling", "completed"];
const workflowStageMeta = {
    memory: {
        label: "Profile",
        description: "DOCQ checks age, allergies, conditions, and previous visits when available.",
    },
    intake: {
        label: "Concern",
        description: "Tell DOCQ what is happening in your own words.",
    },
    followup: {
        label: "Questions",
        description: "DOCQ asks a few care questions to understand urgency better.",
    },
    triage: {
        label: "Urgency",
        description: "DOCQ reviews the information to recommend a safe next step.",
    },
    decision: {
        label: "Next Step",
        description: "DOCQ explains whether to seek urgent care, choose a doctor, or book a visit.",
    },
    scheduling: {
        label: "Doctor",
        description: "Choose a doctor or let DOCQ find the earliest available appointment.",
    },
    completed: {
        label: "Ready",
        description: "Your next care options are ready.",
    },
};

const dateTimeFormatter = new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
});

const dateOnlyFormatter = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
});

function parseDate(value) {
    if (!value) {
        return null;
    }
    const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T09:00:00` : value.replace(" ", "T");
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatDisplayDate(value, mode = "datetime") {
    const parsed = parseDate(value);
    if (!parsed) {
        return value || "";
    }
    return mode === "date" ? dateOnlyFormatter.format(parsed) : dateTimeFormatter.format(parsed);
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function getWorkspacePatient() {
    if (!workspaceProfile?.dataset?.patientName) {
        return null;
    }
    return {
        patient_name: workspaceProfile.dataset.patientName || "",
        patient_email: workspaceProfile.dataset.patientEmail || "",
        phone: workspaceProfile.dataset.phone || "",
        patient_age: workspaceProfile.dataset.patientAge || "",
        conditions: workspaceProfile.dataset.patientConditions || "",
        allergies: workspaceProfile.dataset.patientAllergies || "",
        last_visit: workspaceProfile.dataset.lastVisit || "",
    };
}

function isPatientAuthenticated() {
    return workspaceSession?.dataset?.isPatientAuthenticated === "true";
}

function updateWorkspaceProfile(profile, preferences = {}) {
    if (!workspaceProfile || !profile) {
        return;
    }
    workspaceProfile.dataset.patientName = profile.patient_name || "";
    workspaceProfile.dataset.patientEmail = profile.patient_email || "";
    workspaceProfile.dataset.phone = profile.phone || "";
    workspaceProfile.dataset.patientAge = String(profile.patient_age ?? "");
    workspaceProfile.dataset.patientConditions = profile.chronic_conditions || "";
    workspaceProfile.dataset.patientAllergies = profile.allergies || "";
    workspaceProfile.dataset.lastVisit = profile.last_visit_at || "";
    renderCommunicationStrip(preferences);
}

function renderCommunicationStrip(preferences = null) {
    if (!communicationTimelineStrip || !communicationPreferenceCopy) {
        return;
    }
    const resolvedPreferences = preferences || latestIntake?.communication_preferences || {};
    communicationTimelineStrip.classList.remove("hidden");
    const channels = [
        resolvedPreferences.whatsapp ? "WhatsApp" : null,
        resolvedPreferences.sms ? "SMS" : null,
        resolvedPreferences.email ? "Email" : null,
    ].filter(Boolean);
    communicationPreferenceCopy.textContent = channels.length
        ? `DOCQ will use ${channels.join(", ")} for confirmations, reminders, and scheduling continuity.`
        : "DOCQ will display communication history and reminder acknowledgments here.";
}

const WHATSAPP_SANDBOX_ONBOARDING_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function normalizedOnboardingPhone(phone) {
    return String(phone || "").replace(/\D/g, "") || "unknown";
}

function whatsappSandboxOnboardingKey(onboarding, patientPhone = "") {
    return [
        "docq",
        "whatsapp-sandbox-onboarding",
        onboarding?.sandbox_number || "sandbox",
        onboarding?.join_code || "join",
        normalizedOnboardingPhone(patientPhone),
    ].join(":");
}

function recentlyOpenedWhatsAppSandboxOnboarding(onboarding, patientPhone = "") {
    try {
        const rawValue = window.localStorage.getItem(whatsappSandboxOnboardingKey(onboarding, patientPhone));
        const openedAt = Number(rawValue || 0);
        return openedAt > 0 && Date.now() - openedAt < WHATSAPP_SANDBOX_ONBOARDING_TTL_MS;
    } catch {
        return false;
    }
}

function rememberWhatsAppSandboxOnboarding(onboarding, patientPhone = "") {
    try {
        window.localStorage.setItem(whatsappSandboxOnboardingKey(onboarding, patientPhone), String(Date.now()));
    } catch {
        // Ignore storage restrictions; WhatsApp can still be opened normally.
    }
}

function maybeLaunchWhatsAppSandboxOnboarding(onboarding, feedbackElement = null, patientPhone = "") {
    if (!onboarding?.required || !onboarding?.join_url) {
        return;
    }
    if (recentlyOpenedWhatsAppSandboxOnboarding(onboarding, patientPhone)) {
        return;
    }
    const instruction = `To activate WhatsApp confirmations and reminder notifications, send "${onboarding.join_message || "the join code"}" to ${onboarding.sandbox_number || "the Twilio sandbox number"}. DOCQ will redirect you to WhatsApp next.`;
    if (feedbackElement) {
        feedbackElement.textContent = instruction;
        feedbackElement.className = "mt-3 rounded-card bg-[rgba(59,123,248,0.12)] px-3 py-3 text-sm text-[#9FC2FF]";
    }
    addBubble(
        instruction,
        "bot",
    );
    addActionBubble(
        "WhatsApp will open with the join message prefilled. Tap Send there to activate notifications for this number.",
        [
            {
                label: "Open WhatsApp Join",
                onClick: () => {
                    rememberWhatsAppSandboxOnboarding(onboarding, patientPhone);
                    window.location.assign(onboarding.join_url);
                },
            },
        ],
    );
    window.setTimeout(() => {
        rememberWhatsAppSandboxOnboarding(onboarding, patientPhone);
        window.location.assign(onboarding.join_url);
    }, 1200);
}

function updateCharacterCounter() {
    if (!chatCharCounter || !symptomsField) {
        return;
    }
    chatCharCounter.textContent = `${symptomsField.value.length} / 1200`;
}

function createMessageBubble(content, role = "bot", meta = "") {
    const wrapper = document.createElement("div");
    wrapper.className = `message-enter flex items-end gap-3 ${role === "user" ? "justify-end" : ""}`;
    if (role !== "user") {
        const avatar = document.createElement("div");
        avatar.className = "flex h-9 w-9 items-center justify-center rounded-full bg-[rgba(59,123,248,0.12)] text-xs font-medium text-[#3B7BF8]";
        avatar.textContent = "DQ";
        wrapper.appendChild(avatar);
    }
    const bubble = document.createElement("div");
    bubble.className = role === "user"
        ? "max-w-[85%] rounded-[16px] rounded-br-[6px] border border-white/10 bg-[#3B7BF8] px-4 py-3 text-white"
        : "max-w-[85%] rounded-[16px] rounded-bl-[6px] border border-white/10 bg-[#13171F] px-4 py-3";
    bubble.innerHTML = `
        <p class="mb-1 text-[11px] uppercase tracking-[0.18em] ${role === "user" ? "text-white/70" : "text-[#8B90A0]"}">${role === "user" ? "Patient" : "DOCQ"}</p>
        <p class="text-sm leading-relaxed">${escapeHtml(content)}</p>
        ${meta ? `<p class="mt-2 text-[11px] ${role === "user" ? "text-white/65" : "text-[#8B90A0]"}">${escapeHtml(meta)}</p>` : ""}
    `;
    wrapper.appendChild(bubble);
    return wrapper;
}

function addBubble(content, role = "bot", meta = "") {
    if (!chatStream) {
        return null;
    }
    const bubble = createMessageBubble(content, role, meta);
    chatStream.appendChild(bubble);
    chatStream.scrollTop = chatStream.scrollHeight;
    return bubble;
}

function addActionBubble(content, actions = []) {
    if (!chatStream) {
        return null;
    }
    const bubble = createMessageBubble(content, "bot");
    if (actions.length > 0) {
        const actionRow = document.createElement("div");
        actionRow.className = "mt-3 flex flex-wrap gap-2";
        actions.forEach((action) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "inline-flex h-9 items-center justify-center rounded-full border border-white/10 bg-[#1A1F2E] px-4 text-sm font-medium text-[#F0F2F7]";
            button.textContent = action.label;
            button.addEventListener("click", action.onClick);
            actionRow.appendChild(button);
        });
        bubble.lastElementChild?.insertAdjacentElement("afterend", actionRow);
    }
    chatStream.appendChild(bubble);
    chatStream.scrollTop = chatStream.scrollHeight;
    return bubble;
}

function showTypingIndicator() {
    if (!chatStream) {
        return null;
    }
    const wrapper = document.createElement("div");
    wrapper.className = "message-enter flex items-end gap-3";
    wrapper.dataset.typing = "true";
    wrapper.innerHTML = `
        <div class="flex h-9 w-9 items-center justify-center rounded-full bg-[rgba(59,123,248,0.12)] text-xs font-medium text-[#3B7BF8]">DQ</div>
        <div class="max-w-[85%] rounded-[16px] rounded-bl-[6px] border border-white/10 bg-[#13171F] px-4 py-3">
            <p class="mb-1 text-[11px] uppercase tracking-[0.18em] text-[#8B90A0]">DOCQ</p>
            <div class="flex items-center gap-2">
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
            </div>
        </div>
    `;
    chatStream.appendChild(wrapper);
    chatStream.scrollTop = chatStream.scrollHeight;
    return wrapper;
}

function removeTypingIndicator(node) {
    node?.remove();
}

function openDrawer(stageKey) {
    if (!drawer) {
        return;
    }
    drawerVisible = true;
    drawer.classList.remove("drawer-closed");
    drawer.classList.add("drawer-open");
    drawerStageTitle.textContent = workflowStageMeta[stageKey]?.label || "Care Guidance";
}

function closeDrawer() {
    if (!drawer) {
        return;
    }
    drawerVisible = false;
    drawer.classList.remove("drawer-open");
    drawer.classList.add("drawer-closed");
}

function toggleSection(section, show) {
    if (!section) {
        return;
    }
    section.classList.toggle("hidden", !show);
}

function renderWorkflowStepper(currentStage = "memory") {
    if (!workflowStepper) {
        return;
    }
    const currentIndex = workflowStageOrder.indexOf(currentStage);
    workflowStepper.innerHTML = "";
    workflowStageOrder.forEach((key, index) => {
        const meta = workflowStageMeta[key];
        const state = index < currentIndex ? "complete" : index === currentIndex ? "active" : "upcoming";
        const item = document.createElement("button");
        item.type = "button";
        item.className = "flex flex-col items-center gap-2 text-center";
        const lineLeft = index === 0 ? "transparent" : state !== "upcoming" ? "#3B7BF8" : "rgba(255,255,255,0.12)";
        const lineRight = index === workflowStageOrder.length - 1 ? "transparent" : index < currentIndex ? "#3B7BF8" : "rgba(255,255,255,0.12)";
        let dotClass = "h-3.5 w-3.5 rounded-full border border-white/15 bg-white/10";
        let icon = "";
        if (state === "complete") {
            dotClass = "stage-pulse flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[#34D399] text-[9px] text-[#0C0E14]";
            icon = "✓";
        } else if (state === "active") {
            dotClass = "stage-pulse h-3.5 w-3.5 rounded-full bg-[#3B7BF8] shadow-[0_0_0_6px_rgba(59,123,248,0.16)]";
        }
        item.innerHTML = `
            <div class="flex w-full items-center">
                <span class="h-px flex-1" style="background:${lineLeft}"></span>
                <span class="${dotClass}">${icon}</span>
                <span class="h-px flex-1" style="background:${lineRight}"></span>
            </div>
            <span class="text-[11px] ${state === "active" ? "text-[#F0F2F7]" : state === "complete" ? "text-[#34D399]" : "text-[#8B90A0]"}">${meta.label}</span>
        `;
        item.addEventListener("click", () => {
            if (latestIntake) {
                renderContextDrawer(currentStage, latestIntake);
                openDrawer(currentStage);
            }
        });
        workflowStepper.appendChild(item);
    });
}

function renderMemoryDrawer() {
    const patient = getWorkspacePatient();
    if (!patient || (!patient.conditions && !patient.last_visit)) {
        toggleSection(memorySection, false);
        return;
    }
    toggleSection(memorySection, true);
    memoryConditions.innerHTML = "";
    if (patient.conditions) {
        patient.conditions.split(",").map((item) => item.trim()).filter(Boolean).forEach((condition) => {
            const pill = document.createElement("span");
            pill.className = "rounded-[8px] bg-[rgba(251,191,36,0.12)] px-2.5 py-1 text-xs text-[#fbbf24]";
            pill.textContent = condition;
            memoryConditions.appendChild(pill);
        });
    }
    memoryLastVisit.textContent = patient.last_visit ? formatDisplayDate(patient.last_visit, "datetime") : "No prior visit yet";
    memoryTimeline.innerHTML = "";
    document.querySelectorAll(".timeline-row p").forEach((node, index) => {
        if (index < 3) {
            const item = document.createElement("p");
            item.className = "text-sm leading-relaxed text-[#8B90A0]";
            item.textContent = node.textContent;
            memoryTimeline.appendChild(item);
        }
    });
}

function renderCareTeamDrawer(data) {
    const matches = Array.isArray(data.doctor_matches) ? data.doctor_matches : [];
    if (!matches.length) {
        toggleSection(careTeamSection, false);
        return;
    }
    toggleSection(careTeamSection, true);
    careTeamGrid.innerHTML = "";
    matches.slice(0, 3).forEach((item) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "flex items-center gap-3 rounded-card border border-white/10 bg-[#13171F] px-3 py-3 text-left";
        card.innerHTML = `
            <div class="flex h-10 w-10 items-center justify-center rounded-full bg-[rgba(59,123,248,0.12)] text-xs font-medium text-[#3B7BF8]">${escapeHtml((item.doctor_name || "D").split(" ").map((part) => part[0]).slice(0, 2).join(""))}</div>
            <div class="min-w-0">
                <p class="truncate text-sm font-medium text-[#F0F2F7]">${escapeHtml(item.doctor_name)}</p>
                <p class="text-xs text-[#8B90A0]">${escapeHtml(item.department || item.specialty || data.department || data.specialty || "")}</p>
                <p class="mt-1 text-xs text-[#8B90A0]">${escapeHtml((item.badges || []).join(" • "))}</p>
            </div>
        `;
        card.addEventListener("click", () => switchDoctorRecommendation(item.doctor_name));
        careTeamGrid.appendChild(card);
    });
}

function currentDoctorSelection() {
    return latestIntake?.doctor_name || bookingDoctorName?.value || "";
}

function doctorInitials(name) {
    return (name || "D")
        .split(" ")
        .map((part) => part[0])
        .slice(0, 2)
        .join("")
        .toUpperCase();
}

function doctorCardBadges(match) {
    return Array.isArray(match?.badges) ? match.badges : [];
}

function setSelectedDoctor(match) {
    if (!match) {
        return;
    }
    if (bookingDoctorName) {
        bookingDoctorName.value = match.doctor_name || "";
    }
    if (!latestIntake) {
        return;
    }
    latestIntake = {
        ...latestIntake,
        doctor_name: match.doctor_name || latestIntake.doctor_name,
    };
}

function renderBookingDoctorCards(data) {
    if (!bookingDoctorCards) {
        return;
    }
    const matches = Array.isArray(data.doctor_matches) ? data.doctor_matches : [];
    bookingDoctorCards.innerHTML = "";
    if (bookingDoctorHint) {
        bookingDoctorHint.textContent = matches.length
            ? "Choose a doctor, or keep DOCQ's earliest available recommendation."
            : "DOCQ will use the available doctor for this department.";
    }
    if (!matches.length) {
        return;
    }
    const selectedDoctor = currentDoctorSelection() || data.doctor_name || matches[0]?.doctor_name || "";
    matches.forEach((match) => {
        const selected = match.doctor_name === selectedDoctor;
        const card = document.createElement("button");
        card.type = "button";
        card.className = `min-w-[220px] rounded-card border px-4 py-3 text-left transition ${
            selected
                ? "border-[#3B7BF8] bg-[rgba(59,123,248,0.16)]"
                : "border-white/10 bg-docq-elevated"
        }`;
        card.innerHTML = `
            <div class="flex items-start justify-between gap-3">
                <div class="flex h-11 w-11 items-center justify-center rounded-full bg-[rgba(59,123,248,0.12)] text-xs font-medium text-[#3B7BF8]">${escapeHtml(doctorInitials(match.doctor_name))}</div>
                <span class="text-[11px] ${selected ? "text-[#3B7BF8]" : "text-[#8B90A0]"}">${selected ? "Selected" : "Choose"}</span>
            </div>
            <p class="mt-3 text-sm font-medium text-docq-text">${escapeHtml(match.doctor_name)}</p>
            <p class="mt-1 text-xs text-docq-muted">${escapeHtml(match.department || match.specialty || data.department || "")} · ${escapeHtml(match.branch || "")}</p>
            <div class="mt-3 flex flex-wrap gap-2">
                ${doctorCardBadges(match).map((badge) => `<span class="rounded-pill bg-[rgba(59,123,248,0.12)] px-2.5 py-1 text-[11px] text-[#3B7BF8]">${escapeHtml(badge)}</span>`).join("")}
            </div>
            <p class="mt-3 text-xs text-docq-muted">${escapeHtml(match.selection_reason || (match.previous_visits ? `${match.previous_visits} prior visit(s)` : "New doctor option in this department"))}</p>
            <p class="mt-1 text-xs text-docq-muted">Next: ${escapeHtml(match.next_available_slot || "No live slot available")}</p>
        `;
        card.addEventListener("click", async () => {
            const previousDoctor = currentDoctorSelection();
            setSelectedDoctor(match);
            renderBookingDoctorCards({ ...data, doctor_name: match.doctor_name });
            if (previousDoctor !== match.doctor_name || !(latestIntake?.available_dates || []).length) {
                await switchDoctorRecommendation(match.doctor_name, { silent: true });
            }
        });
        bookingDoctorCards.appendChild(card);
    });
}

function renderQuickAidDrawer(data) {
    const aid = Array.isArray(data.quick_aid) ? data.quick_aid.filter(Boolean) : [];
    if (!aid.length) {
        toggleSection(quickAidSection, false);
        return;
    }
    toggleSection(quickAidSection, true);
    quickAidBody.className = `mt-3 rounded-card px-3 py-3 text-sm leading-relaxed text-[#F0F2F7] ${data.urgency === "Emergency" ? "bg-[rgba(248,113,113,0.12)] text-[#F87171]" : "bg-[rgba(59,123,248,0.12)]"}`;
    quickAidBody.textContent = aid.join(" ");
}

function renderAutomationDrawer(stageKey, data) {
    toggleSection(automationSection, true);
    automationStageName.textContent = workflowStageMeta[stageKey]?.label || "Next Step";
    if (data.urgency === "Emergency" || data.booking_mode === "emergency") {
        automationStageCopy.textContent = "Your symptoms require urgent medical attention. Routine booking is paused so you can use emergency options.";
    } else if (data.booking_mode === "urgent") {
        automationStageCopy.textContent = "DOCQ found a prompt review path. You can choose a doctor or request the earliest available appointment.";
    } else if (data.requires_review) {
        automationStageCopy.textContent = "A care-team review is recommended before final confirmation. You can still choose a preferred doctor.";
    } else {
        automationStageCopy.textContent = "DOCQ found appointment options. Choose a doctor or continue with the earliest available option.";
    }
}

function renderContextDrawer(stageKey, data) {
    renderAutomationDrawer(stageKey, data);
    renderMemoryDrawer();
    renderCareTeamDrawer(data);
    renderQuickAidDrawer(data);
}

function setBookingDates(dates, urgent = false) {
    if (!bookingDate) {
        return;
    }
    bookingDate.innerHTML = '<option value="" selected disabled>Select preferred date</option>';
    dates.forEach((item, index) => {
        const option = document.createElement("option");
        option.value = item.date;
        option.textContent = `${formatDisplayDate(`${item.date}T${item.first_time || "09:00"}`, "datetime")}${urgent && index === 0 ? " · nearest" : ""}`;
        bookingDate.appendChild(option);
    });
    if (urgent && dates[0]) {
        bookingDate.value = dates[0].date;
    }
}

function resetBookingFeedback() {
    if (!bookingFeedback) {
        return;
    }
    bookingFeedback.textContent = "";
    bookingFeedback.className = "mt-3 hidden rounded-card px-3 py-3 text-sm";
}

function showBookingModal() {
    bookingModal?.classList.remove("hidden");
    bookingModal?.classList.add("flex");
}

function hideBookingModal() {
    bookingModal?.classList.add("hidden");
    bookingModal?.classList.remove("flex");
}

function showSignupModal() {
    signupModal?.classList.remove("hidden");
    signupModal?.classList.add("flex");
}

function hideSignupModal() {
    signupModal?.classList.add("hidden");
    signupModal?.classList.remove("flex");
}

function showProfileModal() {
    const patient = getWorkspacePatient();
    if (!patient) {
        return;
    }
    document.getElementById("profile-patient-name").value = patient.patient_name || "";
    document.getElementById("profile-patient-age").value = patient.patient_age || "";
    document.getElementById("profile-phone").value = patient.phone || "";
    profileModal?.classList.remove("hidden");
    profileModal?.classList.add("flex");
}

function hideProfileModal() {
    profileModal?.classList.add("hidden");
    profileModal?.classList.remove("flex");
}

function updateSchedulingReadiness(data) {
    if (!summaryStrip || !summaryReadinessCopy || !openBookingModalButton) {
        return;
    }
    const hasDates = Array.isArray(data.available_dates) && data.available_dates.length > 0;
    const isEmergency = data.urgency === "Emergency" || data.booking_mode === "emergency";
    const canSchedule = hasDates && !isEmergency;
    summaryStrip.classList.remove("hidden");
    summaryStrip.classList.add("flex");
    if (isEmergency) {
        summaryReadinessCopy.textContent = "DOCQ marked this as emergency. Routine booking is paused; use immediate emergency support.";
    } else if (canSchedule && data.requires_review) {
        summaryReadinessCopy.textContent = `DOCQ found priority review slots with ${data.doctor_name}.`;
    } else if (canSchedule) {
        summaryReadinessCopy.textContent = `DOCQ marked the case ready for scheduling with ${data.doctor_name}.`;
    } else {
        summaryReadinessCopy.textContent = "DOCQ is waiting for appointment options before scheduling can continue.";
    }
    openBookingModalButton.disabled = !canSchedule;
    openBookingModalButton.textContent = data.requires_review && !isEmergency ? "Request Review Slot" : "Book Appointment";
    if (bookingSpecialty) {
        bookingSpecialty.value = data.specialty || "";
    }
    if (bookingDoctorName) {
        bookingDoctorName.value = data.doctor_name || "";
    }
    if (bookingSymptoms) {
        bookingSymptoms.value = data.symptoms || symptomsField?.value?.trim() || "";
    }
    if (bookingAge) {
        bookingAge.value = data.known_context?.used_age || getWorkspacePatient()?.patient_age || "";
    }
    const patient = getWorkspacePatient();
    if (patient) {
        const nameField = document.getElementById("patient-name");
        const emailField = document.getElementById("patient-email");
        const phoneField = document.getElementById("patient-phone");
        if (nameField) nameField.value = patient.patient_name || "";
        if (emailField) emailField.value = patient.patient_email || "";
        if (phoneField) phoneField.value = patient.phone || "";
    }
    setBookingDates(data.available_dates || [], data.booking_mode === "urgent");
    renderBookingDoctorCards(data);
    if (data.communication_preferences) {
        renderCommunicationStrip(data.communication_preferences);
    }
}

function getPatientFacingStatus(data) {
    if (data.urgency === "Emergency") {
        return "This case has been prioritized for urgent review.";
    }
    if (data.booking_mode === "urgent") {
        return "DOCQ is escalating the case toward a faster internal review path.";
    }
    if (data.requires_review) {
        return "DOCQ is holding the case for care-team review before scheduling.";
    }
    return "DOCQ is ready to continue toward appointment scheduling.";
}

async function requestSuggestedAppointment(date, label) {
    if (!latestIntake) {
        return;
    }
    const patient = getWorkspacePatient();
    if (!patient || !patient.patient_name || !patient.phone || !isPatientAuthenticated()) {
        addBubble("DOCQ needs you to complete patient signup before it can preserve this triage flow into booking and reminders.", "bot");
        showSignupModal();
        return;
    }
    addBubble(`Please request ${label}.`, "user");
    const typingBubble = showTypingIndicator();
    try {
        console.info("[DOCQ BOOKING REQUEST]", { date, label, doctor_name: latestIntake.doctor_name });
        const response = await fetch("/api/public-booking", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken || "",
            },
            body: JSON.stringify({
                ...patient,
                specialty: latestIntake.specialty,
                doctor_name: currentDoctorSelection() || latestIntake.doctor_name,
                appointment_date: date,
                symptoms: latestIntake.symptoms || bookingSymptoms?.value?.trim() || "",
                clinical_questionnaire: latestIntake.clinical_questionnaire || {},
                vitals: latestIntake.vitals || latestIntake.known_context?.vitals_evaluation?.vitals || {},
            }),
        });
        const rawBody = await response.text();
        let data = {};
        try {
            data = rawBody ? JSON.parse(rawBody) : {};
        } catch {
            throw new Error(`DOCQ booking failed with status ${response.status}.`);
        }
        if (!response.ok) {
            throw new Error(data.error || "Booking failed.");
        }
        if (!data.appointment || !data.appointment.id) {
            throw new Error("DOCQ booking did not return a confirmed appointment payload.");
        }
        removeTypingIndicator(typingBubble);
        console.info("[DOCQ BOOKING CONFIRMED]", { appointment_id: data.appointment.id, workflow_id: data.appointment.workflow_id || "" });
        addBubble(`Appointment request captured. ${data.message}`, "bot", `Ref ${data.appointment.id}`);
        maybeLaunchWhatsAppSandboxOnboarding(data.whatsapp_onboarding, null, patient.phone);
        hideBookingModal();
    } catch (error) {
        removeTypingIndicator(typingBubble);
        console.error("[DOCQ BOOKING ERROR]", error);
        addBubble(error.message || "DOCQ could not request the appointment right now.", "bot");
    }
}

async function switchDoctorRecommendation(doctorName, options = {}) {
    if (!latestIntake) {
        return;
    }
    if (!options.silent) {
        addBubble(`Show me availability for ${doctorName}.`, "user");
    }
    const typingBubble = showTypingIndicator();
    try {
        const response = await fetch(`/api/doctor-availability?doctor_name=${encodeURIComponent(doctorName)}`, {
            headers: { "X-CSRF-Token": csrfToken || "" },
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Availability lookup failed.");
        }
        removeTypingIndicator(typingBubble);
        latestIntake = {
            ...latestIntake,
            doctor_name: doctorName,
            available_dates: data.available_dates || [],
            next_slot: data.available_dates?.[0] ? `${data.available_dates[0].date} ${data.available_dates[0].first_time}` : "No live slot available",
        };
        if (bookingDoctorName) {
            bookingDoctorName.value = doctorName;
        }
        updateSchedulingReadiness(latestIntake);
        renderCareTeamDrawer(latestIntake);
        if (!options.silent) {
            addBubble(`DOCQ updated the recommendation to ${doctorName}.`, "bot", latestIntake.next_slot);
        }
        openDrawer("scheduling");
    } catch (error) {
        removeTypingIndicator(typingBubble);
        if (!options.silent) {
            addBubble(error.message || "DOCQ could not switch doctors right now.", "bot");
        }
    }
}

function offerChatActions(data) {
    const actions = [];
    const isEmergency = data.urgency === "Emergency" || data.booking_mode === "emergency";
    if (isEmergency) {
        actions.push({
            label: "Call Emergency Support",
            onClick: () => window.location.assign("tel:108"),
        });
        actions.push({
            label: "View Care Guidance",
            onClick: () => {
                renderContextDrawer("scheduling", data);
                openDrawer("scheduling");
            },
        });
    }
    if (Array.isArray(data.available_dates) && data.available_dates[0]) {
        const first = data.available_dates[0];
        actions.push({
            label: data.requires_review ? "Choose doctor or priority slot" : "Choose doctor or time",
            onClick: () => {
                updateSchedulingReadiness(data);
                showBookingModal();
            },
        });
        actions.push({
            label: `${data.requires_review ? "Request" : "Book"} ${formatDisplayDate(`${first.date}T${first.first_time || "09:00"}`, "datetime")}`,
            onClick: () => requestSuggestedAppointment(first.date, `${first.date} at ${first.first_time}`),
        });
    }
    if ((data.doctor_matches || []).length > 1) {
        actions.push({
            label: "View available doctors",
            onClick: () => {
                renderCareTeamDrawer(data);
                openDrawer("scheduling");
            },
        });
    }
    if (actions.length) {
        const actionCopy = data.urgency === "Emergency" || data.booking_mode === "emergency"
            ? "Choose an urgent support option."
            : `DOCQ found ${data.department || data.specialty || "care"} options. You can choose a doctor or continue with the earliest available time.`;
        addActionBubble(actionCopy, actions);
    }
}

async function submitPatientSignup(event) {
    event.preventDefault();
    if (!signupFeedback) {
        return;
    }
    const payload = {
        name: document.getElementById("signup-name")?.value.trim(),
        email: document.getElementById("signup-email")?.value.trim(),
        password: document.getElementById("signup-password")?.value,
        phone: document.getElementById("signup-phone")?.value.trim(),
        patient_age: document.getElementById("signup-age")?.value.trim(),
        gender: document.getElementById("signup-gender")?.value.trim(),
        emergency_contact: document.getElementById("signup-emergency-contact")?.value.trim(),
        chronic_conditions: getWorkspacePatient()?.conditions || "",
        allergies: getWorkspacePatient()?.allergies || "",
        prefers_sms: document.getElementById("prefers-sms")?.checked ?? true,
        prefers_email: document.getElementById("prefers-email")?.checked ?? true,
        prefers_whatsapp: document.getElementById("prefers-whatsapp")?.checked ?? true,
        resume_context: {
            workflow_id: latestIntake?.workflow_id || "",
            symptoms: latestIntake?.symptoms || symptomsField?.value.trim() || "",
            specialty: latestIntake?.specialty || "",
            doctor_name: latestIntake?.doctor_name || "",
        },
    };
    try {
        const submit = document.getElementById("signup-submit");
        submit.disabled = true;
        const response = await fetch("/api/auth/patient-signup", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken || "" },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Signup failed.");
        }
        workspaceSession.dataset.isPatientAuthenticated = "true";
        const profile = data.workspace_context?.profile;
        updateWorkspaceProfile(profile, data.workspace_context?.communication_preferences || {});
        latestIntake = { ...latestIntake, communication_preferences: data.workspace_context?.communication_preferences || {} };
        signupFeedback.textContent = "Patient profile created. DOCQ preserved your triage context and can continue into scheduling.";
        signupFeedback.className = "mt-3 rounded-card bg-[rgba(52,211,153,0.12)] px-3 py-3 text-sm text-[#34D399]";
        addBubble("Your patient profile is ready. DOCQ preserved this triage flow and can continue into scheduling and reminders.", "bot");
        maybeLaunchWhatsAppSandboxOnboarding(data.whatsapp_onboarding, signupFeedback, payload.phone);
        if (!data.whatsapp_onboarding?.required) {
            hideSignupModal();
        }
        if (latestIntake && !data.whatsapp_onboarding?.required) {
            updateSchedulingReadiness(latestIntake);
            showBookingModal();
        }
    } catch (error) {
        signupFeedback.textContent = error.message || "Signup failed.";
        signupFeedback.className = "mt-3 rounded-card bg-[rgba(248,113,113,0.12)] px-3 py-3 text-sm text-[#F87171]";
    } finally {
        const submit = document.getElementById("signup-submit");
        if (submit) submit.disabled = false;
    }
}

async function submitProfilePreferences(event) {
    event.preventDefault();
    if (!profileFeedback) {
        return;
    }
    try {
        const response = await fetch("/api/patient/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken || "" },
            body: JSON.stringify({
                patient_name: document.getElementById("profile-patient-name")?.value.trim(),
                patient_age: document.getElementById("profile-patient-age")?.value.trim(),
                phone: document.getElementById("profile-phone")?.value.trim(),
                emergency_contact: document.getElementById("profile-emergency-contact")?.value.trim(),
                prefers_sms: document.getElementById("profile-prefers-sms")?.checked ?? true,
                prefers_email: document.getElementById("profile-prefers-email")?.checked ?? true,
                prefers_whatsapp: document.getElementById("profile-prefers-whatsapp")?.checked ?? true,
            }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Profile update failed.");
        }
        updateWorkspaceProfile(data.workspace_context?.profile, data.workspace_context?.communication_preferences || {});
        profileFeedback.textContent = "Preferences updated.";
        profileFeedback.className = "mt-3 rounded-card bg-[rgba(52,211,153,0.12)] px-3 py-3 text-sm text-[#34D399]";
        hideProfileModal();
    } catch (error) {
        profileFeedback.textContent = error.message || "Profile update failed.";
        profileFeedback.className = "mt-3 rounded-card bg-[rgba(248,113,113,0.12)] px-3 py-3 text-sm text-[#F87171]";
    }
}

async function runIntake(message) {
    addBubble(message, "user");
    const typingBubble = showTypingIndicator();
    renderWorkflowStepper("intake");
    try {
        const response = await fetch("/api/intake", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken || "",
            },
            body: JSON.stringify({ message }),
        });
        const rawBody = await response.text();
        let data = {};
        try {
            data = rawBody ? JSON.parse(rawBody) : {};
        } catch {
            throw new Error(`DOCQ returned an unexpected response (${response.status}).`);
        }
        if (!response.ok) {
            throw new Error(data.error || `DOCQ intake failed with status ${response.status}.`);
        }
        removeTypingIndicator(typingBubble);
        if (data.needs_more_info) {
            renderWorkflowStepper("followup");
            addBubble(data.follow_up_question || "DOCQ needs one more detail before triage can continue.", "bot");
            renderContextDrawer("followup", { requires_review: true, booking_mode: "review", quick_aid: [] });
            openDrawer("followup");
            if (symptomsField) {
                symptomsField.value = "";
                symptomsField.placeholder = "Type the requested follow-up detail here";
                updateCharacterCounter();
                symptomsField.focus();
            }
            return;
        }

        latestIntake = data;
        const currentStage = data.current_stage || "completed";
        renderWorkflowStepper(currentStage);
        renderContextDrawer(currentStage, data);
        openDrawer(currentStage);
        addBubble(data.patient_message || getPatientFacingStatus(data), "bot");
        if (data.continuity_reason) {
            addBubble(data.continuity_reason, "bot");
        }
        if (Array.isArray(data.quick_aid) && data.quick_aid.length) {
            addBubble(`Immediate guidance: ${data.quick_aid.join(" ")}`, "bot");
        }
        updateSchedulingReadiness(data);
        offerChatActions(data);
        if (symptomsField) {
            symptomsField.placeholder = "Describe any new symptoms or continue the conversation";
        }
    } catch (error) {
        removeTypingIndicator(typingBubble);
        addBubble(error.message || "DOCQ could not analyze the intake right now.", "bot");
    }
}

if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener("click", () => {
        sidebar.classList.toggle("sidebar-collapsed");
    });
}

closeDrawerButton?.addEventListener("click", closeDrawer);
openBookingModalButton?.addEventListener("click", showBookingModal);
closeBookingModalButton?.addEventListener("click", hideBookingModal);
openSignupModalButton?.addEventListener("click", showSignupModal);
closeSignupModalButton?.addEventListener("click", hideSignupModal);
openProfileModalButton?.addEventListener("click", showProfileModal);
closeProfileModalButton?.addEventListener("click", hideProfileModal);
bookingModal?.addEventListener("click", (event) => {
    if (event.target === bookingModal) {
        hideBookingModal();
    }
});
signupModal?.addEventListener("click", (event) => {
    if (event.target === signupModal) {
        hideSignupModal();
    }
});
profileModal?.addEventListener("click", (event) => {
    if (event.target === profileModal) {
        hideProfileModal();
    }
});

if (symptomsField) {
    symptomsField.addEventListener("input", updateCharacterCounter);
    symptomsField.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            intakeForm?.requestSubmit();
        }
    });
    updateCharacterCounter();
}

if (intakeForm) {
    intakeForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = symptomsField?.value.trim();
        if (!message) {
            return;
        }
        await runIntake(message);
        symptomsField.value = "";
        updateCharacterCounter();
    });
}

if (bookingForm) {
    bookingForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        resetBookingFeedback();
        if (!isPatientAuthenticated()) {
            hideBookingModal();
            showSignupModal();
            return;
        }
        const payload = {
            patient_name: document.getElementById("patient-name")?.value.trim(),
            patient_email: document.getElementById("patient-email")?.value.trim(),
            phone: document.getElementById("patient-phone")?.value.trim(),
            patient_age: bookingAge?.value?.trim(),
            specialty: bookingSpecialty?.value,
            doctor_name: currentDoctorSelection() || latestIntake?.doctor_name,
            appointment_date: bookingDate?.value,
            symptoms: bookingSymptoms?.value.trim(),
            clinical_questionnaire: latestIntake?.clinical_questionnaire || {},
            vitals: latestIntake?.vitals || latestIntake?.known_context?.vitals_evaluation?.vitals || {},
        };
        try {
            bookingSubmit.disabled = true;
            bookingSubmit.textContent = "Submitting...";
            const response = await fetch("/api/public-booking", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken || "",
            },
            body: JSON.stringify(payload),
        });
            const rawBody = await response.text();
            let data = {};
            try {
                data = rawBody ? JSON.parse(rawBody) : {};
            } catch {
                throw new Error(`DOCQ booking failed with status ${response.status}.`);
            }
            if (!response.ok) {
                throw new Error(data.error || "Booking failed.");
            }
            if (!data.appointment || !data.appointment.id) {
                throw new Error("DOCQ booking did not return a confirmed appointment payload.");
            }
            bookingFeedback.textContent = data.message;
            bookingFeedback.className = "mt-3 rounded-card bg-[rgba(52,211,153,0.12)] px-3 py-3 text-sm text-[#34D399]";
            console.info("[DOCQ BOOKING CONFIRMED]", { appointment_id: data.appointment.id, workflow_id: data.appointment.workflow_id || "" });
            addBubble(`Appointment request captured. ${data.message}`, "bot", `Ref ${data.appointment.id}`);
            maybeLaunchWhatsAppSandboxOnboarding(data.whatsapp_onboarding, bookingFeedback, payload.phone);
            hideBookingModal();
        } catch (error) {
            console.error("[DOCQ BOOKING ERROR]", error);
            bookingFeedback.textContent = error.message || "Booking failed.";
            bookingFeedback.className = "mt-3 rounded-card bg-[rgba(248,113,113,0.12)] px-3 py-3 text-sm text-[#F87171]";
        } finally {
            bookingSubmit.disabled = false;
            bookingSubmit.textContent = "Book Appointment";
        }
    });
}

signupForm?.addEventListener("submit", submitPatientSignup);
profileForm?.addEventListener("submit", submitProfilePreferences);

renderWorkflowStepper("memory");
renderMemoryDrawer();
if (isPatientAuthenticated()) {
    renderCommunicationStrip();
}
