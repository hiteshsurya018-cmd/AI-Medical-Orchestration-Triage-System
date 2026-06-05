const intakeForm = document.getElementById("intake-form");
const symptomsField = document.getElementById("symptoms");
const chatStream = document.getElementById("chat-stream");
const workflowStepper = document.getElementById("workflow-stepper");
const drawer = document.getElementById("context-drawer");
const closeDrawerButton = document.getElementById("close-drawer");
const drawerStageTitle = document.getElementById("drawer-stage-title");
const chatCharCounter = document.getElementById("chat-char-counter");
const summaryStrip = document.getElementById("workspace-summary-strip");
const summaryReadinessCopy = document.getElementById("summary-readiness-copy");
const openBookingModalButton = document.getElementById("open-booking-modal");
const bookingModal = document.getElementById("booking-modal");
const closeBookingModalButton = document.getElementById("close-booking-modal");
const bookingForm = document.getElementById("booking-form");
const bookingDate = document.getElementById("booking-date");
const bookingTime = document.getElementById("booking-time");
const bookingPreferSelectedDate = document.getElementById("booking-prefer-selected-date");
const bookingCalendarGrid = document.getElementById("booking-calendar-grid");
const bookingCalendarTitle = document.getElementById("booking-calendar-title");
const bookingCalendarPrev = document.getElementById("booking-calendar-prev");
const bookingCalendarNext = document.getElementById("booking-calendar-next");
const bookingDateDetails = document.getElementById("booking-date-details");
const bookingSlotPanel = document.getElementById("booking-slot-panel");
const bookingSlotGrid = document.getElementById("booking-slot-grid");
const bookingSlotSummary = document.getElementById("booking-slot-summary");
const bookingRecommendation = document.getElementById("booking-recommendation");
const bookingEarliestButton = document.getElementById("booking-earliest-button");
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
const patientPanelSection = document.getElementById("drawer-patient-panel");
const patientPanelBody = document.getElementById("drawer-patient-panel-body");
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
const csrfTokenMeta = document.querySelector('meta[name="csrf-token"]');

function currentCsrfToken() {
    return document.querySelector('input[name="_csrf_token"]')?.value || csrfTokenMeta?.content || "";
}

function updateCsrfToken(token) {
    if (!token) {
        return;
    }
    if (csrfTokenMeta) {
        csrfTokenMeta.content = token;
    }
    document.querySelectorAll('input[name="_csrf_token"]').forEach((input) => {
        input.value = token;
    });
}

let latestIntake = null;
let drawerVisible = false;
let bookingCalendar = null;
let bookingVisibleMonth = null;
let selectedBookingDate = "";
let selectedBookingTime = "";
let preferredBookingDate = "";

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

function localDateFromYmd(value) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return null;
    }
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
}

function ymdFromDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function addDays(date, days) {
    const next = new Date(date);
    next.setDate(next.getDate() + days);
    return next;
}

function clientTodayYmd() {
    return ymdFromDate(new Date());
}

function monthStart(value = "") {
    const base = localDateFromYmd(value) || new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
}

function monthLabel(date) {
    return new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(date);
}

function firstCalendarCell(date) {
    const first = new Date(date.getFullYear(), date.getMonth(), 1);
    return addDays(first, -first.getDay());
}

function calendarDayByDate(dateValue) {
    const days = bookingCalendar?.days || [];
    return days.find((day) => day.date === dateValue) || null;
}

function availabilityCopy(day) {
    if (!day) {
        return { icon: "-", label: "Unavailable", title: "No schedule published" };
    }
    const map = {
        available: { icon: "+", label: "High", title: "High availability", color: "#3DB870" },
        moderate: { icon: "~", label: "Moderate", title: "Moderate availability", color: "#4A9EE8" },
        low: { icon: "!", label: "Limited", title: "Limited availability", color: "#E8A030" },
        booked: { icon: "x", label: "Booked", title: "Fully booked", color: "#E04545" },
        unavailable: { icon: "-", label: "Unavailable", title: day.unavailable_reason || "Doctor unavailable", color: "#3A3E52" },
    };
    return map[day.availability] || map.unavailable;
}

function bookingTooltipHtml(dateValue, day, state) {
    const displayDate = formatDisplayDate(`${dateValue}T${day?.earliest_slot || "09:00"}`, "date");
    const doctorName = bookingCalendar?.doctor?.display_name || bookingCalendar?.doctor?.doctor_name || currentDoctorSelection();
    const waitHint = expectedWaitLabel(day);
    return `
        <span class="booking-tooltip">
            <strong>${escapeHtml(displayDate)}</strong>
            <span><i style="background:${state.color || "#4A9EE8"}"></i>${escapeHtml(doctorName || "DOCQ care team")}</span>
            <span>${escapeHtml(state.title || state.label)}</span>
            <span><i style="background:#3DB870"></i>${day?.available_slots || 0} slot${Number(day?.available_slots || 0) === 1 ? "" : "s"} - Wait ${waitHint}</span>
            <span><i style="background:#4A9EE8"></i>${day?.booked_slots || 0} patients booked</span>
        </span>
    `;
}

function expectedWaitLabel(day) {
    if (!day) {
        return "~8 min";
    }
    return day.queue_load === "High" ? "~25 min" : day.queue_load === "Moderate" ? "~15 min" : "~8 min";
}

function dateAvailabilityLabel(day) {
    if (!day) {
        return "";
    }
    const availableSlots = Number(day.available_slots || 0);
    if (availableSlots > 0) {
        return `${availableSlots} slot${availableSlots === 1 ? "" : "s"}`;
    }
    if (day.availability === "booked") {
        return "Full";
    }
    return "Closed";
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

function scrollChatToBottom() {
    if (!chatStream) {
        return;
    }
    requestAnimationFrame(() => {
        chatStream.scrollTop = chatStream.scrollHeight;
    });
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
        ? "max-w-[78%] rounded-[16px] rounded-br-[6px] border border-white/10 bg-[#3B7BF8] px-4 py-3 text-white"
        : "max-w-[92%] rounded-[16px] rounded-bl-[6px] border border-white/10 bg-[#13171F] px-4 py-3";
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
    scrollChatToBottom();
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
    scrollChatToBottom();
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
        <div class="max-w-[92%] rounded-[16px] rounded-bl-[6px] border border-white/10 bg-[#13171F] px-4 py-3">
            <p class="mb-1 text-[11px] uppercase tracking-[0.18em] text-[#8B90A0]">DOCQ</p>
            <div class="flex items-center gap-2">
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
                <span class="typing-dot h-2 w-2 rounded-full bg-[#3B7BF8]"></span>
            </div>
        </div>
    `;
    chatStream.appendChild(wrapper);
    scrollChatToBottom();
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

function launchAssessmentWithSymptom(symptom = "") {
    const normalizedSymptom = String(symptom || "").trim();
    if (symptomsField && normalizedSymptom) {
        symptomsField.value = normalizedSymptom;
        updateCharacterCounter();
    }
    if (normalizedSymptom && intakeForm) {
        intakeForm.requestSubmit();
        return;
    }
    symptomsField?.focus();
}

function openPatientPanel(panelKey) {
    const template = document.getElementById(`patient-panel-${panelKey}`);
    if (!template || !patientPanelSection || !patientPanelBody) {
        return;
    }
    toggleSection(automationSection, false);
    toggleSection(memorySection, false);
    toggleSection(careTeamSection, false);
    toggleSection(quickAidSection, false);
    patientPanelBody.innerHTML = template.innerHTML;
    toggleSection(patientPanelSection, true);
    openDrawer("patient");
    drawerStageTitle.textContent = template.content?.querySelector("h3")?.textContent || "Patient Workspace";
    patientPanelBody.querySelectorAll("[data-launch-symptom]").forEach((node) => {
        node.addEventListener("click", () => {
            launchAssessmentWithSymptom(node.dataset.launchSymptom || "");
        });
    });
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
    toggleSection(patientPanelSection, false);
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

function setBookingDates(dates, urgent = false) {
    if (!bookingDate) {
        return;
    }
    const first = dates.find((item) => Number(item.open_count || 0) > 0) || dates[0];
    if (first && urgent) {
        preferredBookingDate = first.date;
    }
}

function setBookingCalendar(calendar, options = {}) {
    bookingCalendar = calendar || null;
    if (!bookingCalendar) {
        renderBookingCalendar();
        renderBookingSlots("");
        renderBookingRecommendation(null);
        updateEarliestButtonState();
        updatePreferenceButtonState();
        return;
    }
    const firstAvailable = bookingCalendar.first_available || null;
    const initialDate = options.preferredDate || selectedBookingDate || firstAvailable?.date || bookingCalendar.days?.[0]?.date || clientTodayYmd();
    preferredBookingDate = options.preferredDate || preferredBookingDate || "";
    bookingVisibleMonth = monthStart(initialDate);
    if (selectedBookingDate) {
        const selectedDay = calendarDayByDate(selectedBookingDate);
        if (!selectedDay || Number(selectedDay.available_slots || 0) <= 0) {
            selectBookingDate("", { render: false });
        }
    }
    renderBookingCalendar();
    renderBookingSlots(selectedBookingDate || "");
    renderBookingRecommendation(latestIntake?.recommended_appointment || {
        doctor_name: bookingCalendar.doctor?.doctor_name || currentDoctorSelection(),
        slot: firstAvailable ? `${firstAvailable.date} ${firstAvailable.time}` : "",
        reason: "Earliest available doctor slot based on live calendar capacity.",
        availability_score: latestIntake?.doctor_matches?.find((item) => item.doctor_name === currentDoctorSelection())?.availability_score || 0,
    });
    updateEarliestButtonState();
    updatePreferenceButtonState();
}

function renderBookingRecommendation(recommendation = null) {
    if (!bookingRecommendation) {
        return;
    }
    const rawSlot = String(recommendation?.slot || "");
    const slotMatch = rawSlot.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})/);
    const fallback = bookingCalendar?.first_available || null;
    const datePart = slotMatch?.[1] || fallback?.date || "";
    const timePart = slotMatch?.[2] || fallback?.time || "";
    if (!datePart || !timePart) {
        bookingRecommendation.classList.add("hidden");
        bookingRecommendation.innerHTML = "";
        updateEarliestButtonState();
        return;
    }
    const doctorName = recommendation?.doctor_name || fallback?.doctor_name || currentDoctorSelection();
    const specialty = bookingCalendar?.specialty || bookingSpecialty?.value || latestIntake?.specialty || "Clinical care";
    const day = calendarDayByDate(datePart);
    const availableToday = datePart === clientTodayYmd() ? "Available Today" : formatDisplayDate(`${datePart}T${timePart || "09:00"}`, "date");
    bookingRecommendation.classList.remove("hidden");
    bookingRecommendation.innerHTML = `
        <div>
            <p>DOCQ Recommendation</p>
            <strong>${escapeHtml(doctorName)}</strong>
            <span>${escapeHtml(specialty)}</span>
            <span>${escapeHtml(availableToday)}</span>
        </div>
        <div class="booking-recommendation-meta">
            <span>Next Available: <strong>${escapeHtml(timePart || "Open slot")}</strong></span>
            <span>Expected Wait: <strong>${escapeHtml(expectedWaitLabel(day))}</strong></span>
            <small>${escapeHtml(recommendation?.reason || "Earliest suitable appointment from the live DOCQ calendar.")}</small>
            <button type="button" class="booking-recommendation-action" data-book-recommended="true">Book Recommended</button>
        </div>
    `;
    updateEarliestButtonState();
}

function selectBookingDate(dateValue, options = {}) {
    selectedBookingDate = dateValue || "";
    if (bookingDate) {
        bookingDate.value = selectedBookingDate;
    }
    const day = calendarDayByDate(selectedBookingDate);
    const firstSlot = day?.slots?.find((slot) => slot.available);
    if (options.autoSelectSlot && firstSlot) {
        selectedBookingTime = firstSlot.time;
        if (bookingTime) {
            bookingTime.value = selectedBookingTime;
        }
    } else if (!day?.slots?.some((slot) => slot.time === selectedBookingTime && slot.available)) {
        selectedBookingTime = "";
        if (bookingTime) {
            bookingTime.value = "";
        }
    }
    if (options.render !== false) {
        renderBookingCalendar();
        renderBookingSlots(selectedBookingDate);
        renderBookingDateDetails(selectedBookingDate);
    }
    updatePreferenceButtonState();
    updateBookingSubmitState();
}

function selectBookingTime(timeValue) {
    selectedBookingTime = timeValue || "";
    if (bookingTime) {
        bookingTime.value = selectedBookingTime;
    }
    renderBookingSlots(selectedBookingDate);
    updateBookingSubmitState();
}

function updateBookingSubmitState() {
    if (!bookingSubmit) {
        return;
    }
    if (selectedBookingDate && selectedBookingTime) {
        bookingSubmit.disabled = false;
        bookingSubmit.textContent = `Confirm - ${formatDisplayDate(`${selectedBookingDate}T${selectedBookingTime}`, "datetime")}`;
    } else if (selectedBookingDate) {
        bookingSubmit.disabled = true;
        bookingSubmit.textContent = "Select a time slot to confirm";
    } else {
        bookingSubmit.disabled = true;
        bookingSubmit.textContent = "Select a date and time to confirm";
    }
}

function updatePreferenceButtonState() {
    if (!bookingPreferSelectedDate) {
        return;
    }
    bookingPreferSelectedDate.disabled = !selectedBookingDate;
    bookingPreferSelectedDate.textContent = selectedBookingDate
        ? `Prefer ${formatDisplayDate(`${selectedBookingDate}T09:00`, "date")}`
        : "Use Selected Date";
}

function updateEarliestButtonState() {
    if (!bookingEarliestButton) {
        return;
    }
    const firstAvailable = bookingCalendar?.first_available || null;
    bookingEarliestButton.disabled = !firstAvailable;
    bookingEarliestButton.textContent = firstAvailable
        ? `⚡ Book Earliest Available - ${formatDisplayDate(`${firstAvailable.date}T${firstAvailable.time}`, "datetime")}`
        : "No earliest slot available";
}

function renderBookingDateDetails(dateValue) {
    if (!bookingDateDetails) {
        return;
    }
    const day = calendarDayByDate(dateValue);
    if (!day) {
        bookingDateDetails.classList.add("hidden");
        bookingDateDetails.innerHTML = "";
        return;
    }
    const state = availabilityCopy(day);
    const doctorName = bookingCalendar?.doctor?.display_name || bookingCalendar?.doctor?.doctor_name || currentDoctorSelection() || "DOCQ care team";
    bookingDateDetails.classList.remove("hidden");
    bookingDateDetails.innerHTML = `
        <strong>${escapeHtml(formatDisplayDate(`${dateValue}T${day.earliest_slot || "09:00"}`, "date"))}</strong>
        <span>${escapeHtml(doctorName)}</span>
        <span>${escapeHtml(state.title)} - ${escapeHtml(dateAvailabilityLabel(day))}</span>
        <span>Queue Load: ${escapeHtml(day.queue_load || "Low")} - Expected Wait: ${escapeHtml(expectedWaitLabel(day))}</span>
    `;
}

async function setPreferredBookingDate(dateValue) {
    preferredBookingDate = dateValue || "";
    await refreshBookingAvailability({ preferredDate: preferredBookingDate });
    updatePreferenceButtonState();
}

function bookEarliestAvailable({ submit = true } = {}) {
    const firstAvailable = bookingCalendar?.first_available || null;
    if (!firstAvailable) {
        return;
    }
    selectBookingDate(firstAvailable.date, { autoSelectSlot: true });
    if (submit && bookingForm) {
        if (typeof bookingForm.reportValidity === "function" && !bookingForm.reportValidity()) {
            return;
        }
        bookingForm.requestSubmit();
    }
}

function renderBookingCalendar() {
    if (!bookingCalendarGrid || !bookingCalendarTitle) {
        return;
    }
    const visibleMonth = bookingVisibleMonth || monthStart(selectedBookingDate || clientTodayYmd());
    bookingCalendarTitle.textContent = monthLabel(visibleMonth);
    bookingCalendarGrid.innerHTML = "";
    const start = firstCalendarCell(visibleMonth);
    const today = clientTodayYmd();
    for (let index = 0; index < 42; index += 1) {
        const cellDate = addDays(start, index);
        const dateValue = ymdFromDate(cellDate);
        const day = calendarDayByDate(dateValue);
        const state = availabilityCopy(day);
        const isOutsideMonth = cellDate.getMonth() !== visibleMonth.getMonth();
        const isPast = dateValue < today || day?.is_past;
        const selectable = Boolean(day && Number(day.available_slots || 0) > 0 && !isPast);
        const button = document.createElement("button");
        button.type = "button";
        button.disabled = !selectable;
        button.className = [
            "booking-calendar-day",
            `availability-${day?.availability || "unavailable"}`,
            "cal-cell",
            `s-${day?.availability === "available" ? "high" : day?.availability === "low" ? "limited" : day?.availability || "unavail"}`,
            isOutsideMonth ? "outside-month" : "",
            dateValue === today ? "today" : "",
            dateValue === selectedBookingDate ? "selected picked" : "",
            day?.preferred || preferredBookingDate === dateValue ? "preferred s-preferred" : "",
            day?.recommended ? "recommended s-recommended" : "",
        ].filter(Boolean).join(" ");
        button.title = `${dateValue}\n${state.title}\nAvailable Slots: ${day?.available_slots || 0}\nDoctors Available: ${day?.doctors_available || 0}\nEarliest Slot: ${day?.earliest_slot || "None"}\nQueue Load: ${day?.queue_load || "Unavailable"}`;
        button.innerHTML = `
            <span class="booking-day-number">${cellDate.getDate()}</span>
            <span class="booking-day-state">${day ? escapeHtml(state.icon) : ""} ${day ? escapeHtml(state.label) : ""}</span>
            <span class="booking-day-meta">${escapeHtml(dateAvailabilityLabel(day))}</span>
            ${day ? bookingTooltipHtml(dateValue, day, state) : ""}
        `;
        if (selectable) {
            button.addEventListener("click", () => selectBookingDate(dateValue, { autoSelectSlot: true }));
        }
        bookingCalendarGrid.appendChild(button);
    }
}

function renderBookingSlots(dateValue) {
    if (!bookingSlotGrid || !bookingSlotSummary) {
        return;
    }
    bookingSlotGrid.innerHTML = "";
    const day = calendarDayByDate(dateValue);
    if (!day) {
        bookingSlotPanel?.classList.add("hidden");
        bookingSlotSummary.textContent = "Select a date";
        bookingSlotGrid.innerHTML = "";
        renderBookingDateDetails("");
        updateBookingSubmitState();
        return;
    }
    bookingSlotPanel?.classList.remove("hidden");
    const slots = Array.isArray(day.slots) ? day.slots : [];
    bookingSlotSummary.textContent = `${day.available_slots || 0} available - ${day.booked_slots || 0} booked - ${day.queue_load || "Queue"} load`;
    if (!slots.length) {
        bookingSlotGrid.innerHTML = '<p class="booking-empty-state">No slots published for this date.</p>';
        renderBookingDateDetails(dateValue);
        updateBookingSubmitState();
        return;
    }
    slots.forEach((slot) => {
        const available = Boolean(slot.available);
        const selected = selectedBookingTime === slot.time;
        const button = document.createElement("button");
        button.type = "button";
        button.disabled = !available;
        button.className = `booking-slot-card ${available ? "available" : "unavailable"} ${selected ? "selected" : ""}`;
        button.title = `${slot.time}\n${slot.doctor_name || currentDoctorSelection()}\n${slot.department || bookingSpecialty?.value || ""}\nRoom: ${slot.room || "Care suite"}\nStatus: ${slot.label || slot.status}`;
        button.innerHTML = `
            <strong>${escapeHtml(slot.time)}</strong>
            <span>${available ? (selected ? "Recommended" : "Available") : escapeHtml(slot.label || "Unavailable")}</span>
            <small>${escapeHtml(slot.room || "Care suite")}</small>
        `;
        if (available) {
            button.addEventListener("click", () => selectBookingTime(slot.time));
        }
        bookingSlotGrid.appendChild(button);
    });
    renderBookingDateDetails(dateValue);
    updateBookingSubmitState();
}

function showBookingModal() {
    updateEarliestButtonState();
    updatePreferenceButtonState();
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
    const selectedDoctor = currentDoctorSelection() || data.doctor_name;
    const selectedMatch = (data.doctor_matches || []).find((item) => item.doctor_name === selectedDoctor) || data.doctor_matches?.[0];
    setBookingCalendar(data.calendar || selectedMatch?.calendar || null, { preferredDate: preferredBookingDate });
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

async function requestSuggestedAppointment(date, label, time = "") {
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
                "X-CSRF-Token": currentCsrfToken(),
            },
            body: JSON.stringify({
                ...patient,
                specialty: latestIntake.specialty,
                doctor_name: currentDoctorSelection() || latestIntake.doctor_name,
                appointment_date: date,
                appointment_time: time || selectedBookingTime || "",
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
        const params = new URLSearchParams({
            doctor_name: doctorName,
            preferred_date: preferredBookingDate || selectedBookingDate || "",
            specialty: latestIntake.specialty || bookingSpecialty?.value || "",
        });
        const response = await fetch(`/api/doctor-availability?${params.toString()}`, {
            headers: { "X-CSRF-Token": currentCsrfToken() },
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
            calendar: data.calendar || null,
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

async function refreshBookingAvailability(options = {}) {
    const doctorName = currentDoctorSelection() || latestIntake?.doctor_name;
    if (!doctorName || !latestIntake) {
        return;
    }
    const preferredDate = options.preferredDate ?? preferredBookingDate;
    const startDate = options.startDate || preferredDate || selectedBookingDate || clientTodayYmd();
    try {
        const params = new URLSearchParams({
            doctor_name: doctorName,
            preferred_date: preferredDate || "",
            start_date: startDate || "",
            specialty: latestIntake.specialty || bookingSpecialty?.value || "",
        });
        const response = await fetch(`/api/doctor-availability?${params.toString()}`, {
            headers: { "X-CSRF-Token": currentCsrfToken() },
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Availability refresh failed.");
        }
        latestIntake = {
            ...latestIntake,
            available_dates: data.available_dates || [],
            calendar: data.calendar || null,
            next_slot: data.recommendation ? `${data.recommendation.date} ${data.recommendation.time}` : latestIntake.next_slot,
        };
        setBookingCalendar(data.calendar || null, { preferredDate });
    } catch (error) {
        if (!options.silent) {
            addBubble(error.message || "DOCQ could not refresh availability right now.", "bot");
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
            onClick: () => requestSuggestedAppointment(first.date, `${first.date} at ${first.first_time}`, first.first_time || ""),
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
            headers: { "Content-Type": "application/json", "X-CSRF-Token": currentCsrfToken() },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Signup failed.");
        }
        updateCsrfToken(data.csrf_token);
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
            headers: { "Content-Type": "application/json", "X-CSRF-Token": currentCsrfToken() },
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
                "X-CSRF-Token": currentCsrfToken(),
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

closeDrawerButton?.addEventListener("click", closeDrawer);
document.querySelectorAll("[data-patient-panel]").forEach((node) => {
    node.addEventListener("click", () => openPatientPanel(node.dataset.patientPanel));
});
document.querySelectorAll("[data-launch-symptom]").forEach((node) => {
    node.addEventListener("click", () => launchAssessmentWithSymptom(node.dataset.launchSymptom || ""));
});
openBookingModalButton?.addEventListener("click", () => {
    updateSchedulingReadiness(latestIntake || {});
    showBookingModal();
});
closeBookingModalButton?.addEventListener("click", hideBookingModal);
bookingCalendarPrev?.addEventListener("click", async () => {
    const current = bookingVisibleMonth || monthStart(selectedBookingDate || clientTodayYmd());
    bookingVisibleMonth = new Date(current.getFullYear(), current.getMonth() - 1, 1);
    await refreshBookingAvailability({ startDate: ymdFromDate(bookingVisibleMonth), silent: true });
});
bookingCalendarNext?.addEventListener("click", async () => {
    const current = bookingVisibleMonth || monthStart(selectedBookingDate || clientTodayYmd());
    bookingVisibleMonth = new Date(current.getFullYear(), current.getMonth() + 1, 1);
    await refreshBookingAvailability({ startDate: ymdFromDate(bookingVisibleMonth), silent: true });
});
document.querySelectorAll("[data-preferred-date]").forEach((button) => {
    button.addEventListener("click", async () => {
        const today = localDateFromYmd(clientTodayYmd());
        const value = button.dataset.preferredDate === "tomorrow" ? ymdFromDate(addDays(today, 1)) : clientTodayYmd();
        await setPreferredBookingDate(value);
    });
});
bookingPreferSelectedDate?.addEventListener("click", async () => {
    if (selectedBookingDate) {
        await setPreferredBookingDate(selectedBookingDate);
    }
});
bookingEarliestButton?.addEventListener("click", () => bookEarliestAvailable({ submit: true }));
bookingRecommendation?.addEventListener("click", (event) => {
    if (event.target instanceof HTMLElement && event.target.matches("[data-book-recommended]")) {
        bookEarliestAvailable({ submit: true });
    }
});
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

const launchSymptom = new URLSearchParams(window.location.search).get("symptoms");
if (launchSymptom && symptomsField && intakeForm) {
    const normalizedLaunchSymptom = launchSymptom.trim();
    if (normalizedLaunchSymptom) {
        symptomsField.value = normalizedLaunchSymptom;
        updateCharacterCounter();
        const launchKey = `docq-assessment-launch:${normalizedLaunchSymptom}`;
        if (sessionStorage.getItem(launchKey) !== "started") {
            sessionStorage.setItem(launchKey, "started");
            window.setTimeout(() => {
                intakeForm.requestSubmit();
            }, 250);
        }
    }
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
            appointment_time: bookingTime?.value,
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
                "X-CSRF-Token": currentCsrfToken(),
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
            updateBookingSubmitState();
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
