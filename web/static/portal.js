/**
 * 鍗氶泤璇剧▼鐢ㄦ埛闂ㄦ埛 鈥?浜や簰閫昏緫
 */

// 鈺愨晲鈺愨晲鈺愨晲 State 鈺愨晲鈺愨晲鈺愨晲
let portalState = {
    email: '',
    subscriber: null,
    reminderCourseIds: new Set(),
    notifications: [],
    filteredNotifications: [],
    notificationsHours: 24,
    lastCourseRefreshAt: null,
    shouldShowOnboarding: false,
};
const PORTAL_TAB_KEY = 'portal_active_tab';

function buildPortalCourseParams() {
    const params = new URLSearchParams();
    const keyword = document.getElementById('portalSearch')?.value || '';
    const campus = document.getElementById('portalCampus')?.value || '';
    const selfSign = document.getElementById('portalSelfSign')?.checked || false;
    const showExpired = document.getElementById('portalExpired')?.checked || false;
    const availableNow = document.getElementById('portalAvailableNow')?.checked || false;
    const waitlistOnly = document.getElementById('portalWaitlistOnly')?.checked || false;

    if (keyword) params.set('keyword', keyword);
    if (campus) params.set('campus', campus);
    if (selfSign) params.set('self_sign', 'true');
    if (showExpired) params.set('include_expired', 'true');
    if (availableNow) params.set('available_now', 'true');
    if (waitlistOnly) params.set('waitlist_only', 'true');
    return params;
}

async function loadFilteredCourses() {
    const params = buildPortalCourseParams();
    const qs = params.toString();
    const res = await portalApi(`/api/courses${qs ? `?${qs}` : ''}`);
    if (res.success) {
        const sorted = sortPortalCourses(res.data);
        renderCourses(sorted);
        portalState.lastCourseRefreshAt = new Date();
        renderPortalRefreshMeta();
    }
    return res;
}

function sortPortalCourses(courses) {
    const mode = document.getElementById('portalSort')?.value || 'recommended';
    const list = [...(courses || [])];
    const asTime = (value) => {
        if (!value) return Number.MAX_SAFE_INTEGER;
        const t = new Date(String(value).replace(' ', 'T')).getTime();
        return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : t;
    };

    if (mode === 'remaining_desc') {
        return list.sort((a, b) => (b.remaining || 0) - (a.remaining || 0));
    }
    if (mode === 'enroll_start_asc') {
        return list.sort((a, b) => asTime(a.enroll_start) - asTime(b.enroll_start));
    }
    if (mode === 'course_time_asc') {
        return list.sort((a, b) => asTime(a.start_time) - asTime(b.start_time));
    }

    return list.sort((a, b) => {
        const aExpired = a.expired ? 1 : 0;
        const bExpired = b.expired ? 1 : 0;
        if (aExpired !== bExpired) return aExpired - bExpired;
        const aRemaining = a.remaining || 0;
        const bRemaining = b.remaining || 0;
        const aOpen = aRemaining > 0 ? 0 : 1;
        const bOpen = bRemaining > 0 ? 0 : 1;
        if (aOpen !== bOpen) return aOpen - bOpen;
        if (aOpen === 0 && aRemaining !== bRemaining) return bRemaining - aRemaining;
        return asTime(a.enroll_start) - asTime(b.enroll_start);
    });
}

function renderPortalRefreshMeta() {
    const el = document.getElementById('portalRefreshMeta');
    if (!el) return;
    if (!portalState.lastCourseRefreshAt) {
        el.textContent = '绛夊緟鍒锋柊';
        return;
    }
    const now = Date.now();
    const diffSec = Math.max(0, Math.floor((now - portalState.lastCourseRefreshAt.getTime()) / 1000));
    if (diffSec < 60) {
        el.textContent = '鍒氬垰鏇存柊';
        return;
    }
    const hh = String(portalState.lastCourseRefreshAt.getHours()).padStart(2, '0');
    const mm = String(portalState.lastCourseRefreshAt.getMinutes()).padStart(2, '0');
    el.textContent = `${hh}:${mm} 鏇存柊`;
}

// 鈺愨晲鈺愨晲鈺愨晲 Init 鈺愨晲鈺愨晲鈺愨晲
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const email = params.get('email') || '';
    portalState.email = email;
    document.getElementById('userEmail').textContent = email || '\u52a0\u8f7d\u4e2d...';
    initTabs();
    const savedTab = localStorage.getItem(PORTAL_TAB_KEY);
    if (savedTab) switchPortalTab(savedTab);
    loadPortalData();
    setInterval(renderPortalRefreshMeta, 30000);
});

function toggleFeedbackFloat() {
    const box = document.getElementById('portalFeedbackFloat');
    if (!box) return;
    box.classList.toggle('expanded');
}

function toggleWelcomeBanner() {
    const banner = document.getElementById('welcomeBanner');
    const btn = document.getElementById('portalHelpButton');
    if (!banner) return;
    const shouldOpen = !banner.classList.contains('mobile-open');
    banner.classList.toggle('mobile-open', shouldOpen);
    btn?.classList.toggle('active', shouldOpen);
}

function dismissBanner() {
    const banner = document.getElementById('welcomeBanner');
    const btn = document.getElementById('portalHelpButton');
    if (!banner) return;
    banner.classList.remove('mobile-open');
    btn?.classList.remove('active');
}

function toggleFirstRunSettingsHint(visible) {
    const tip = document.getElementById('firstRunSettingsHint');
    if (!tip) return;
    tip.hidden = !visible;
}

function openPortalOnboarding() {
    const overlay = document.getElementById('portalOnboardingOverlay');
    if (!overlay) return;
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add('open'));
}

function closePortalOnboarding() {
    const overlay = document.getElementById('portalOnboardingOverlay');
    if (!overlay) return;
    overlay.classList.remove('open');
    setTimeout(() => {
        overlay.hidden = true;
    }, 180);
}

async function markPortalOnboardingSeen() {
    if (!portalState.shouldShowOnboarding) return;
    portalState.shouldShowOnboarding = false;
    try {
        await portalApi('/api/subscriber/session/onboarding-seen', { method: 'POST' });
    } catch (err) {
        console.error('mark onboarding failed', err);
    }
}

async function finishPortalOnboarding(target = 'browse') {
    await markPortalOnboardingSeen();
    closePortalOnboarding();

    if (target === 'settings') {
        switchPortalTab('manage');
        toggleFirstRunSettingsHint(true);
        const card = document.getElementById('settingsCard');
        if (card) {
            card.classList.add('first-run-focus');
            card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            setTimeout(() => card.classList.remove('first-run-focus'), 2200);
        }
        showPortalToast('\u8fd9\u91cc\u5c31\u662f\u504f\u597d\u8bbe\u7f6e\u533a\uff0c\u6309\u9700\u4fee\u6539\u5c31\u884c\uff0c\u4e0d\u7528\u4e00\u6b21\u914d\u5f97\u5f88\u590d\u6742\u3002', 'info');
        return;
    }

    switchPortalTab('courses');
    toggleFirstRunSettingsHint(false);
    showPortalToast('\u5148\u6d4f\u89c8\u8bfe\u7a0b\u5c31\u884c\uff0c\u60f3\u8bbe\u5f97\u66f4\u7cbe\u51c6\u518d\u53bb\u300c\u63d0\u9192 & \u8bbe\u7f6e\u300d\u3002', 'info');
}

function toggleMobileFilters(forceOpen = null) {
    const extra = document.getElementById('portalFilterExtra');
    const toggle = document.getElementById('portalFilterToggle');
    if (!extra || !toggle) return;

    const shouldOpen = forceOpen == null ? extra.classList.contains('is-collapsed') : forceOpen;
    extra.classList.toggle('is-collapsed', !shouldOpen);
    toggle.textContent = shouldOpen ? '\u6536\u8d77\u7b5b\u9009' : '\u66f4\u591a\u7b5b\u9009';
    toggle.classList.toggle('active', shouldOpen);
}

// API Helper
async function portalApi(url, options = {}) {
    const { suppressErrorToast = false, headers = {}, ...fetchOptions } = options;
    try {
        const resp = await fetch(url, {
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                ...headers,
            },
            ...fetchOptions,
        });

        const contentType = (resp.headers.get('content-type') || '').toLowerCase();
        if (contentType.includes('application/json')) {
            const data = await resp.json();
            if (!resp.ok && data && typeof data === 'object') {
                data.success = false;
                if (!data.error) data.error = `请求失败 (${resp.status})`;
            }
            return data;
        }

        const rawText = await resp.text();
        const bodyPreview = String(rawText || '').replace(/\s+/g, ' ').trim().slice(0, 120);
        const statusLabel = `${resp.status}${resp.statusText ? ` ${resp.statusText}` : ''}`.trim();
        console.error('Portal non-JSON API response:', {
            url,
            status: resp.status,
            contentType,
            bodyPreview,
        });
        if (!suppressErrorToast) {
            showPortalToast('接口返回异常页面，请稍后重试', 'error');
        }
        return {
            success: false,
            error: `接口返回了非 JSON 响应 (${statusLabel || 'unknown'})`,
            status: resp.status,
            bodyPreview,
        };
    } catch (err) {
        console.error('API Error:', err);
        if (!suppressErrorToast) {
            showPortalToast('缃戠粶璇锋眰澶辫触', 'error');
        }
        return { success: false, error: err.message };
    }
}

// 鈺愨晲鈺愨晲鈺愨晲 Data Loading 鈺愨晲鈺愨晲鈺愨晲
async function loadPortalData() {
    const emailQuery = portalState.email ? `?email=${encodeURIComponent(portalState.email)}` : '';
    const sessionRes = await portalApi(`/api/subscriber/session${emailQuery}`);
    if (!sessionRes.success) {
        if (sessionRes.status === 401) {
            window.location.href = '/subscribe';
            return;
        }
        showPortalToast(sessionRes.error || '加载门户数据失败', 'error');
        return;
    }
    portalState.subscriber = sessionRes.data;
    portalState.shouldShowOnboarding = Boolean(sessionRes.data.show_onboarding);

    // Load in parallel
    const [coursesRes, remindersRes, categoriesRes, highlightsRes] = await Promise.all([
        portalApi('/api/courses'),
        portalApi('/api/subscriber/session/reminders'),
        portalApi('/api/categories'),
        portalApi('/api/portal/highlights'),
    ]);

    document.getElementById('userEmail').textContent = portalState.subscriber.email || '\u5df2\u767b\u5f55';
    renderSettings(portalState.subscriber, categoriesRes.success ? categoriesRes.data : []);
    toggleFirstRunSettingsHint(portalState.shouldShowOnboarding);

    // Highlights
    if (highlightsRes.success) {
        renderPortalHighlights(highlightsRes.data);
    }

    // Courses
    if (coursesRes.success) {
        const activeCourses = coursesRes.data.filter(c => !c.expired);
        const availableCourses = activeCourses.filter(c => c.remaining > 0);
        document.getElementById('heroCount').textContent = availableCourses.length;
        await loadFilteredCourses();
    }

    // Reminders
    if (remindersRes.success) {
        const highlightReminderItems = highlightsRes.success && highlightsRes.data && Array.isArray(highlightsRes.data.pending_reminder_items)
            ? highlightsRes.data.pending_reminder_items
            : [];
        const reminderItems = remindersRes.data.length ? remindersRes.data : highlightReminderItems;
        portalState.reminderCourseIds = new Set(reminderItems.map(r => r.course_id));
        renderReminders(reminderItems);
        // Update reminder count badge
        const pendingCount = reminderItems.filter(r => !r.sent).length;
        const badge = document.getElementById('reminderBadge');
        if (pendingCount > 0) {
            badge.textContent = pendingCount;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }

    await reloadNotifications();

    if (portalState.shouldShowOnboarding) {
        switchPortalTab('courses');
        openPortalOnboarding();
    }
}

// 鈺愨晲鈺愨晲鈺愨晲 Tabs 鈺愨晲鈺愨晲鈺愨晲
function initTabs() {
    document.querySelectorAll('.portal-tab').forEach(tab => {
        tab.addEventListener('click', () => switchPortalTab(tab.dataset.tab));
    });
}

function switchPortalTab(tabName) {
    document.querySelectorAll('.portal-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.portal-tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`panel-${tabName}`).classList.add('active');
    localStorage.setItem(PORTAL_TAB_KEY, tabName);
}

// 鈺愨晲鈺愨晲鈺愨晲 Course Rendering 鈺愨晲鈺愨晲鈺愨晲
function renderCourses(courses) {
    const grid = document.getElementById('courseGrid');
    if (!courses || !courses.length) {
        grid.innerHTML = `
            <div class="portal-empty" style="grid-column: 1/-1;">
                <div class="portal-empty-icon">馃摥</div>
                <div class="portal-empty-text">鏆傛棤璇剧▼</div>
                <div class="portal-empty-hint">绛夊緟绯荤粺鎶撳彇鏂拌绋?/div>
            </div>`;
        return;
    }

    // 鍒嗕负鏈夊悕棰?/ 宸叉弧
    const available = courses.filter(c => c.remaining > 0 || c.expired);
    const full = courses.filter(c => c.remaining <= 0 && !c.expired);

    let html = '';

    // 鏈夊悕棰濈殑璇剧▼
    if (available.length > 0) {
        html += available.map(c => renderCourseCard(c)).join('');
    } else {
        html += `<div class="portal-empty" style="grid-column:1/-1;">
            <div class="portal-empty-icon">馃帀</div>
            <div class="portal-empty-text">鎵€鏈夎绋嬮兘宸叉弧鍛?/div>
            <div class="portal-empty-hint">鏈夐€€璇惧悕棰濇椂绯荤粺浼氱珛鍗抽€氱煡浣?/div>
        </div>`;
    }

    grid.innerHTML = html;

    // 宸叉弧璇剧▼鎶樺彔鍖?    let fullSection = document.getElementById('fullCoursesSection');
    if (full.length > 0) {
        if (!fullSection) {
            fullSection = document.createElement('div');
            fullSection.id = 'fullCoursesSection';
            grid.parentNode.insertBefore(fullSection, grid.nextSibling);
        }
        fullSection.innerHTML = `
            <div class="portal-full-toggle" onclick="toggleFullCourses()">
                <span class="portal-full-toggle-icon" id="fullToggleIcon">鈻?/span>
                <span>宸叉弧璇剧▼</span>
                <span class="portal-full-count">${full.length}</span>
                <span class="portal-full-hint">鏈夐€€璇炬椂绯荤粺鑷姩鍗虫椂鎺ㄩ€?/span>
            </div>
            <div class="portal-full-grid" id="fullCoursesGrid" style="display:none;">
                ${full.map(c => renderCourseCard(c, true)).join('')}
            </div>`;
    } else if (fullSection) {
        fullSection.remove();
    }
}

function toggleFullCourses() {
    const grid = document.getElementById('fullCoursesGrid');
    const icon = document.getElementById('fullToggleIcon');
    if (!grid) return;
    const isHidden = grid.style.display === 'none';
    grid.style.display = isHidden ? 'grid' : 'none';
    icon.textContent = isHidden ? '\u25b2' : '\u25bc';
}

function renderCourseCard(course, isFull = false) {
    const checkIn = course.check_in_method || course.sign_method || '';
    const isSelf = checkIn.includes('\u81ea\u4e3b');
    const signBadge = isSelf
        ? '<span class="portal-badge portal-badge-self">\u2713 \u81ea\u4e3b\u7b7e\u5230</span>'
        : `<span class="portal-badge portal-badge-regular">${escapeHtml(checkIn || '\u5e38\u89c4\u7b7e\u5230')}</span>`;

    const remaining = course.remaining;
    const capacity = course.capacity || 1;
    const fillPct = Math.min(100, ((course.enrolled || 0) / capacity) * 100);
    let fillClass = 'green';
    if (remaining <= 0) fillClass = 'red';
    else if (remaining <= 10) fillClass = 'yellow';

    const isReminded = portalState.reminderCourseIds.has(course.id);
    const canSetReminder = !course.expired && remaining > 0;
    const remindBtn = !canSetReminder
        ? ''
        : isReminded
            ? `<button class="portal-btn-remind reminded" disabled>\u2713 \u5df2\u8bbe\u63d0\u9192</button>`
            : `<button class="portal-btn-remind" onclick="registerReminder('${escapeHtml(course.id)}', this)">\ud83d\udd14 \u63d0\u9192\u6211\u9009\u8bfe</button>`;

    const cardClass = isFull ? 'portal-course-card portal-card-full' : 'portal-course-card';
    const fullBadge = (remaining <= 0 && !course.expired) ? '<span class="portal-badge portal-badge-full">\u5df2\u6ee1</span>' : '';
    const statusBadge = buildPortalCourseStatus(course);
    const timingHint = buildPortalCourseTimingHint(course);
    const goAction = buildPortalCoursePrimaryAction(course);

    return `
    <div class="${cardClass}">
        <div class="portal-card-top">
            <span class="portal-course-name">${escapeHtml(course.name)}</span>
            ${fullBadge}
            ${signBadge}
        </div>
        ${statusBadge ? `<div class="portal-card-status-row">${statusBadge}</div>` : ''}
        ${timingHint ? `<div class="portal-card-timing-hint">${timingHint}</div>` : ''}
        <div class="portal-card-meta">
            <span class="portal-badge portal-badge-category">${escapeHtml(course.category)}</span>
            &nbsp;${escapeHtml(course.teacher)} \u00b7 ${escapeHtml(course.campus)}
        </div>
        <div class="portal-card-details">
            <span class="portal-detail-label">\ud83d\udccd \u5730\u70b9</span>
            <span>${escapeHtml(course.location)}</span>
            <span class="portal-detail-label">\u23f0 \u8bfe\u7a0b</span>
            <span>${escapeHtml(course.start_time)} ~ ${escapeHtml(course.end_time)}</span>
            <span class="portal-detail-label">\ud83d\udcdd \u9009\u8bfe</span>
            <span>${escapeHtml(course.enroll_start)} ~ ${escapeHtml(course.enroll_end)}</span>
        </div>
        <div class="portal-capacity-bar">
            <div class="portal-capacity-fill ${fillClass}" style="width:${fillPct}%"></div>
        </div>
        <div class="portal-capacity-text">
            <span>\u5df2\u9009 ${course.enrolled}/${capacity}</span>
            <span>\u5269\u4f59 ${remaining} \u4eba</span>
        </div>
        <div class="portal-card-actions${!remindBtn ? ' single' : ''}">
            ${goAction}
            ${remindBtn}
        </div>
    </div>`;
}

function buildPortalCoursePrimaryAction(course) {
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    const canOpenNow = !course.expired && Number(course.remaining || 0) > 0 && enrollStart && !Number.isNaN(enrollStart.getTime()) && enrollStart.getTime() <= Date.now();
    const staleHot = isPortalCourseStale(course) && isPortalCourseHot(course);
    const label = staleHot
        ? '\u7acb\u5373\u6838\u5b9e\u540d\u989d'
        : canOpenNow
            ? '\u7acb\u5373\u53bb\u9009\u8bfe'
            : '\u6253\u5f00\u9009\u8bfe\u95e8\u6237';
    const className = canOpenNow ? 'portal-btn-go primary' : 'portal-btn-go';
    return `<a class="${className}" href="https://bykc.buaa.edu.cn/" target="_blank" rel="noopener">${label}</a>`;
}

function getPortalCourseAgeSeconds(course) {
    const value = Number(course.last_seen_seconds_ago ?? -1);
    return Number.isFinite(value) && value >= 0 ? value : null;
}

function isPortalCourseHot(course) {
    const remaining = Number(course.remaining || 0);
    return remaining > 0 && remaining <= 3;
}

function isPortalCourseStale(course) {
    const ageSeconds = getPortalCourseAgeSeconds(course);
    return ageSeconds !== null && ageSeconds >= 90;
}

function buildPortalCourseStatus(course) {
    const remaining = Number(course.remaining || 0);
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    const now = new Date();
    const timeValue = enrollStart && !Number.isNaN(enrollStart.getTime()) ? enrollStart.getTime() : null;
    const minutesToStart = timeValue == null ? null : Math.floor((timeValue - now.getTime()) / 60000);

    if (course.expired) {
        return '<span class="portal-status-pill muted">\u5df2\u8fc7\u671f</span>';
    }
    if (remaining <= 0) {
        return '<span class="portal-status-pill wait">\u5df2\u6ee1\u8e72\u9000\u9009</span>';
    }
    if (isPortalCourseStale(course) && isPortalCourseHot(course)) {
        return '<span class="portal-status-pill warn">\u540d\u989d\u53d8\u5316\u5feb</span>';
    }
    if (minutesToStart !== null && minutesToStart <= 0) {
        return '<span class="portal-status-pill hot">\u53ef\u7acb\u5373\u5c1d\u8bd5</span>';
    }
    if (minutesToStart !== null && minutesToStart <= 180) {
        return '<span class="portal-status-pill soon">\u5373\u5c06\u5f00\u62a2</span>';
    }
    if (remaining >= 20) {
        return '<span class="portal-status-pill easy">\u540d\u989d\u8f83\u5145\u8db3</span>';
    }
    if (remaining <= 5) {
        return '<span class="portal-status-pill tight">\u540d\u989d\u7d27\u5f20</span>';
    }
    return '<span class="portal-status-pill normal">\u53ef\u52a0\u5165\u5173\u6ce8</span>';
}

function buildPortalCourseTimingHint(course) {
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    const hints = [];

    if (enrollStart && !Number.isNaN(enrollStart.getTime()) && !course.expired) {
        const diffMinutes = Math.floor((enrollStart.getTime() - Date.now()) / 60000);
        if (diffMinutes <= 0) {
            const opened = Math.abs(diffMinutes);
            if (opened < 60) {
                hints.push(`\u5df2\u5f00\u9009 ${opened} \u5206\u949f`);
            } else {
                const hours = Math.floor(opened / 60);
                const mins = opened % 60;
                hints.push(mins ? `\u5df2\u5f00\u9009 ${hours} \u5c0f\u65f6 ${mins} \u5206` : `\u5df2\u5f00\u9009 ${hours} \u5c0f\u65f6`);
            }
        } else if (diffMinutes < 60) {
            hints.push(`${diffMinutes} \u5206\u949f\u540e\u5f00\u62a2`);
        } else if (diffMinutes < 1440) {
            const hours = Math.floor(diffMinutes / 60);
            const mins = diffMinutes % 60;
            hints.push(mins ? `${hours} \u5c0f\u65f6 ${mins} \u5206\u540e\u5f00\u62a2` : `${hours} \u5c0f\u65f6\u540e\u5f00\u62a2`);
        } else {
            const days = Math.floor(diffMinutes / 1440);
            const hours = Math.floor((diffMinutes % 1440) / 60);
            hints.push(hours ? `${days} \u5929 ${hours} \u5c0f\u65f6\u540e\u5f00\u62a2` : `${days} \u5929\u540e\u5f00\u62a2`);
        }
    }

    const ageSeconds = getPortalCourseAgeSeconds(course);
    if (isPortalCourseStale(course) && ageSeconds !== null) {
        const ageMinutes = Math.max(1, Math.floor(ageSeconds / 60));
        hints.push(`\u6570\u636e\u66f4\u65b0\u4e8e ${ageMinutes} \u5206\u949f\u524d\uff0c\u540d\u989d\u53ef\u80fd\u5df2\u53d8\u5316`);
    }

    return hints.join(' \u00b7 ');
}

function filterCourses() {
    clearTimeout(courseSearchTimeout);
    courseSearchTimeout = setTimeout(async () => {
        await loadFilteredCourses();
    }, 350);
}

function togglePortalQuickFilter(mode) {
    const availableEl = document.getElementById('portalAvailableNow');
    const waitlistEl = document.getElementById('portalWaitlistOnly');
    if (!availableEl || !waitlistEl) {
        filterCourses();
        return;
    }

    if (mode === 'available' && availableEl.checked) {
        waitlistEl.checked = false;
    }
    if (mode === 'waitlist' && waitlistEl.checked) {
        availableEl.checked = false;
    }
    filterCourses();
}

async function refreshPortalCourses(btnEl) {
    if (!btnEl) return;
    btnEl.disabled = true;
    const originalText = btnEl.textContent;
    btnEl.textContent = '\u5237\u65b0\u4e2d...';

    const requestedAt = Date.now();
    const result = await portalApi('/api/portal/refresh', { method: 'POST' });
    if (result.success) {
        if (result.joined_existing) {
            showPortalToast('\u540e\u53f0\u5df2\u6709\u5237\u65b0\u4efb\u52a1\uff0c\u6b63\u5728\u4e3a\u4f60\u540c\u6b65\u6700\u65b0\u7ed3\u679c', 'info');
        } else {
            showPortalToast('\u5df2\u5f00\u59cb\u5237\u65b0\u8bfe\u7a0b\uff0c\u7a0d\u540e\u81ea\u52a8\u66f4\u65b0', 'info');
        }
        await waitForPortalRefresh(requestedAt);
    } else {
        showPortalToast(result.error || '鍒锋柊澶辫触', 'error');
    }

    btnEl.disabled = false;
    btnEl.textContent = originalText;
}

async function waitForPortalRefresh(requestedAt) {
    const deadline = Date.now() + 70000;
    const threshold = requestedAt - 5000;

    while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const statusRes = await portalApi('/api/status', { suppressErrorToast: true });
        if (!statusRes.success) continue;

        const data = statusRes.data || {};
        if (data.is_running) continue;

        const lastSuccess = data.last_success
            ? new Date(String(data.last_success).replace(' ', 'T')).getTime()
            : 0;
        const lastRun = data.last_run
            ? new Date(String(data.last_run).replace(' ', 'T')).getTime()
            : 0;

        if ((lastSuccess && lastSuccess >= threshold) || (lastRun && lastRun >= threshold)) {
            await loadPortalData();
            showPortalToast('\u8bfe\u7a0b\u5217\u8868\u5df2\u540c\u6b65\u6700\u65b0\u7ed3\u679c', 'success');
            return;
        }
    }

    showPortalToast('\u540e\u53f0\u4ecd\u5728\u5237\u65b0\uff0c\u7a0d\u540e\u4f1a\u81ea\u52a8\u663e\u793a\u6700\u65b0\u7ed3\u679c', 'info');
}

// 鈺愨晲鈺愨晲鈺愨晲 Register Reminder 鈺愨晲鈺愨晲鈺愨晲
async function registerReminder(courseId, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '\u6ce8\u518c\u4e2d...';

    const res = await portalApi(`/api/remind/${courseId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    });

    if (res.success) {
        portalState.reminderCourseIds.add(courseId);
        btnEl.textContent = '\u2713 \u5df2\u8bbe\u63d0\u9192';
        btnEl.classList.add('reminded');
        showPortalToast('\u9009\u8bfe\u63d0\u9192\u5df2\u6ce8\u518c\uff0c\u5f00\u59cb\u524d 5 \u5206\u949f\u901a\u77e5\u4f60', 'success');

        // Refresh reminders
        const remRes = await portalApi('/api/subscriber/session/reminders');
        if (remRes.success) renderReminders(remRes.data);
    } else {
        btnEl.disabled = false;
        btnEl.textContent = '\ud83d\udd14 \u63d0\u9192\u6211\u9009\u8bfe';
        showPortalToast('\u6ce8\u518c\u5931\u8d25\uff1a' + (res.error || ''), 'error');
    }
}

// 鈺愨晲鈺愨晲鈺愨晲 Settings Rendering 鈺愨晲鈺愨晲鈺愨晲
function renderSettings(sub, categories) {
    // Campus
    const campusEl = document.getElementById('settingsCampus');
    if (campusEl) campusEl.value = sub.campus_filter || '';

    // Self-sign toggle
    const selfSignEl = document.getElementById('settingsSelfSign');
    if (selfSignEl) selfSignEl.checked = sub.self_sign_only;

    // Active toggle
    const activeEl = document.getElementById('settingsActive');
    if (activeEl) activeEl.checked = sub.active;

    // Category chips
    const chipRow = document.getElementById('settingsCategories');
    if (chipRow && categories.length) {
        const selectedCats = sub.categories || [];
        chipRow.innerHTML = categories.map(cat => {
            const isActive = selectedCats.includes(cat) ? 'active' : '';
            return `<span class="portal-chip ${isActive}" onclick="this.classList.toggle('active')">${escapeHtml(cat)}</span>`;
        }).join('');
    }
}

async function saveSettings() {
    const btn = document.getElementById('btnSaveSettings');
    btn.disabled = true;
    btn.textContent = '\u4fdd\u5b58\u4e2d...';

    const selectedCats = Array.from(document.querySelectorAll('#settingsCategories .portal-chip.active'))
        .map(c => c.textContent.trim());

    const payload = {
        campus_filter: document.getElementById('settingsCampus').value,
        self_sign_only: document.getElementById('settingsSelfSign').checked,
        active: document.getElementById('settingsActive').checked,
        categories: selectedCats,
    };

    const res = await portalApi('/api/subscriber/session', {
        method: 'PUT',
        body: JSON.stringify(payload),
    });

    if (res.success) {
        showPortalToast('\u504f\u597d\u8bbe\u7f6e\u5df2\u4fdd\u5b58', 'success');
        portalState.subscriber = res.data;
        toggleFirstRunSettingsHint(false);
    } else {
        showPortalToast('\u4fdd\u5b58\u5931\u8d25\uff1a' + (res.error || ''), 'error');
    }

    btn.disabled = false;
    btn.textContent = '淇濆瓨璁剧疆';
}

// 鈺愨晲鈺愨晲鈺愨晲 Reminders Rendering 鈺愨晲鈺愨晲鈺愨晲
function renderReminders(reminders) {
    const container = document.getElementById('reminderList');
    if (!reminders || !reminders.length) {
        container.innerHTML = `
            <div class="portal-empty">
                <div class="portal-empty-icon">\ud83d\udd14</div>
                <div class="portal-empty-text">\u6682\u65e0\u9009\u8bfe\u63d0\u9192</div>
                <div class="portal-empty-hint">\u5728\u8bfe\u7a0b\u9875\u9762\u70b9\u51fb\u300c\u63d0\u9192\u6211\u9009\u8bfe\u300d\u6ce8\u518c\u63d0\u9192</div>
            </div>`;
        return;
    }
    container.innerHTML = reminders.map(r => `
        <div class="portal-reminder-item">
            <div class="portal-reminder-info">
                <div class="portal-reminder-name">${escapeHtml(r.course_name)}</div>
                <div class="portal-reminder-meta">
                    ${escapeHtml(r.course_category)} \u00b7 ${escapeHtml(r.course_teacher)}
                    \u00b7 \u9009\u8bfe\u5f00\u59cb ${escapeHtml(r.enroll_start)}
                </div>
            </div>
            <span class="portal-reminder-status ${r.sent ? 'sent' : 'pending'}">
                ${r.sent ? '\u2713 \u5df2\u901a\u77e5' : '\u7b49\u5f85\u4e2d'}
            </span>
        </div>
    `).join('');
}

// 鈺愨晲鈺愨晲鈺愨晲 Notification Center 鈺愨晲鈺愨晲鈺愨晲
function renderNotifications(notifications) {
    const container = document.getElementById('notificationTimeline');
    if (!notifications || !notifications.length) {
        container.innerHTML = `
            <div class="portal-empty">
                <div class="portal-empty-icon">馃摥</div>
                <div class="portal-empty-text">鏈€杩?${portalState.notificationsHours} 灏忔椂鏆傛棤鎺ㄩ€佽褰?/div>
                <div class="portal-empty-hint">绯荤粺鍙戦€侀€氱煡鍚庝細鍦ㄨ繖閲屾樉绀烘椂闂寸嚎</div>
            </div>`;
        return;
    }

    const deliveryModeLabel = {
        'priority': '\u5373\u65f6\u63d0\u9192',
        'digest_urgent': '\u8fd1\u671f\u6458\u8981',
        'digest_soon': '\u65b0\u8bfe\u6458\u8981',
        'digest_daily': '\u6bcf\u65e5\u6c47\u603b',
    };

    container.innerHTML = notifications.map(item => {
        const typeClass = item.event_type === 'snipe' ? 'snipe' : 'new';
        const typeText = item.event_type === 'snipe' ? '\u9000\u8bfe\u8865\u4f4d' : '\u65b0\u53d1\u73b0';
        const statusText = item.success ? '\u5df2\u9001\u8fbe' : '\u53d1\u9001\u5931\u8d25';
        const statusClass = item.success ? 'success' : 'failed';
        const dm = item.delivery_mode || '';
        const modeLabel = item.event_type === 'snipe' ? '' : (deliveryModeLabel[dm] || '');
        const modeBadge = modeLabel
            ? `<span class="portal-notify-mode ${escapeHtml(dm)}">${modeLabel}</span>`
            : '';
        return `
        <div class="portal-notify-item">
            <div class="portal-notify-main">
                <div class="portal-notify-title">${escapeHtml(item.course_name || '\u672a\u77e5\u8bfe\u7a0b')}</div>
                <div class="portal-notify-meta">
                    ${escapeHtml(item.course_category || '\u672a\u5206\u7c7b')} \u00b7 ${escapeHtml(item.sent_at || '')}
                </div>
            </div>
            <div class="portal-notify-badges">
                <span class="portal-notify-type ${typeClass}">${typeText}</span>
                ${modeBadge}
                <span class="portal-notify-status ${statusClass}">${statusText}</span>
            </div>
        </div>`;
    }).join('');
}

function applyNotificationFilters() {
    const type = document.getElementById('notifyTypeFilter')?.value || '';
    const status = document.getElementById('notifyStatusFilter')?.value || '';
    const keyword = (document.getElementById('notifyKeywordFilter')?.value || '').trim().toLowerCase();

    const filtered = (portalState.notifications || []).filter(item => {
        if (type && item.event_type !== type) return false;
        if (status === 'success' && !item.success) return false;
        if (status === 'failed' && item.success) return false;
        if (keyword) {
            const hay = `${item.course_name || ''} ${item.course_category || ''}`.toLowerCase();
            if (!hay.includes(keyword)) return false;
        }
        return true;
    });

    portalState.filteredNotifications = filtered;
    renderNotifications(filtered);
}

async function reloadNotifications() {
    const hours = Number(document.getElementById('notifyRangeFilter')?.value || portalState.notificationsHours || 24);
    portalState.notificationsHours = Math.max(1, Math.min(168, hours));
    const res = await portalApi(`/api/subscriber/session/notifications?hours=${portalState.notificationsHours}&limit=300`);
    if (res.success) {
        portalState.notifications = res.data || [];
        applyNotificationFilters();
    }
}

function exportNotificationsCsv() {
    const rows = portalState.filteredNotifications || [];
    if (!rows.length) {
        showPortalToast('娌℃湁鍙鍑虹殑閫氱煡璁板綍', 'error');
        return;
    }
    const header = ['sent_at', 'course_name', 'course_category', 'event_type', 'status', 'channel', 'message'];
    const lines = [header.join(',')];
    for (const item of rows) {
        const cells = [
            item.sent_at || '',
            item.course_name || '',
            item.course_category || '',
            item.event_type || '',
            item.success ? 'success' : 'failed',
            item.channel || '',
            item.message || '',
        ].map(csvEscape);
        lines.push(cells.join(','));
    }
    const csv = lines.join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    a.download = `notifications-${portalState.notificationsHours}h-${ts}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

// 鈺愨晲鈺愨晲鈺愨晲 Portal Highlights 鈺愨晲鈺愨晲鈺愨晲
let _upcomingTimer = null;
let _upcomingData = [];
let _upcomingExpanded = false;
let _upcomingTimerStartedAt = Date.now() / 1000;

function renderPortalHighlights(data) {
    const upcoming = data.upcoming_courses || [];
    const todayNew = data.today_new_count ?? 0;
    const pendingReminders = data.pending_reminders ?? 0;
    const pausedUntil = data.push_paused_until || null;

    const upcomingEl = document.getElementById('hlUpcomingCount');
    const subEl = document.getElementById('hlUpcomingSub');
    const todayEl = document.getElementById('hlTodayNew');
    const remindersEl = document.getElementById('hlPendingReminders');
    if (upcomingEl) upcomingEl.textContent = upcoming.length;
    if (subEl) subEl.textContent = upcoming.length === 0 ? '24 \u5c0f\u65f6\u5185\u65e0\u5f00\u62a2\u8bfe\u7a0b' : `${upcoming.length} \u95e8\u8bfe\u7a0b\u5373\u5c06\u5f00\u59cb`;
    if (todayEl) todayEl.textContent = todayNew;
    if (remindersEl) remindersEl.textContent = pendingReminders;

    const section = document.getElementById('upcomingCoursesSection');
    const list = document.getElementById('upcomingCoursesList');
    if (section && list) {
        if (upcoming.length > 0) {
            _upcomingData = upcoming;
            _upcomingExpanded = false;
            section.style.display = 'block';
            _renderUpcomingSummary();
            _renderUpcomingItems();
            _startUpcomingTimer();
        } else {
            _upcomingData = [];
            _upcomingExpanded = false;
            section.style.display = 'none';
            list.innerHTML = '';
            if (_upcomingTimer) {
                clearInterval(_upcomingTimer);
                _upcomingTimer = null;
            }
        }
    }

    _updatePausePushUI(pausedUntil);
}

function _renderUpcomingSummary() {
    const summaryTextEl = document.getElementById('upcomingCoursesSummaryText');
    const countEl = document.getElementById('upcomingCoursesSummaryCount');
    const toggleEl = document.getElementById('upcomingCoursesToggle');
    const summaryEl = document.getElementById('upcomingCoursesSummary');
    if (!summaryTextEl || !countEl || !toggleEl || !summaryEl) return;

    const count = _upcomingData.length;
    countEl.textContent = String(count);
    toggleEl.textContent = _upcomingExpanded ? '\u6536\u8d77' : '\u5c55\u5f00';
    summaryEl.classList.toggle('expanded', _upcomingExpanded);

    if (!count) {
        summaryTextEl.textContent = '24 \u5c0f\u65f6\u5185\u6682\u65f6\u65e0\u5f00\u62a2\u8bfe\u7a0b';
        return;
    }

    const first = _upcomingData[0];
    const elapsed = Math.floor((Date.now() / 1000) - _upcomingTimerStartedAt);
    const secsLeft = Math.max(0, Number(first.seconds_left || 0) - elapsed);
    const pieces = [first.name || '\u8fd1\u671f\u5f00\u62a2\u8bfe\u7a0b'];
    if (secsLeft > 0) {
        pieces.push(`${_formatCountdown(secsLeft)} \u540e\u5f00\u62a2`);
    }
    if (count > 1) {
        pieces.push(`\u53e6\u6709 ${count - 1} \u95e8`);
    }
    summaryTextEl.textContent = pieces.join(' \u00b7 ');
}

function _renderUpcomingItems() {
    const list = document.getElementById('upcomingCoursesList');
    if (!list) return;

    list.classList.toggle('collapsed', !_upcomingExpanded);
    if (!_upcomingData.length) {
        list.innerHTML = '';
        return;
    }

    list.innerHTML = _upcomingData.map((c) => {
        const secsLeft = Math.max(0, Number(c.seconds_left || 0) - Math.floor((Date.now() / 1000) - _upcomingTimerStartedAt));
        const isUrgent = secsLeft < 3600;
        return `
        <div class="portal-upcoming-item">
            <div class="portal-upcoming-info">
                <div class="portal-upcoming-name">${escapeHtml(c.name)}</div>
                <div class="portal-upcoming-meta">${escapeHtml(c.campus)} 路 ${escapeHtml(c.category)} 路 鍓╀綑 ${c.remaining} 浜?/div>
            </div>
            <div class="portal-upcoming-countdown ${isUrgent ? 'urgent' : ''}" data-secs="${secsLeft}">
                ${_formatCountdown(secsLeft)}
            </div>
        </div>`;
    }).join('');
}

function toggleUpcomingCourses() {
    _upcomingExpanded = !_upcomingExpanded;
    _renderUpcomingSummary();
    _renderUpcomingItems();
}

function _startUpcomingTimer() {
    if (_upcomingTimer) clearInterval(_upcomingTimer);
    _upcomingTimerStartedAt = Date.now() / 1000;
    _upcomingTimer = setInterval(() => {
        const countdowns = document.querySelectorAll('.portal-upcoming-countdown');
        countdowns.forEach((el) => {
            const secs = Math.max(0, parseInt(el.dataset.secs || '0', 10) - 1);
            el.dataset.secs = String(secs);
            el.textContent = _formatCountdown(secs);
            el.className = `portal-upcoming-countdown${secs < 3600 ? ' urgent' : ''}`;
        });
        _renderUpcomingSummary();
    }, 1000);
}

function _formatCountdown(secs) {
    const s = Math.max(0, secs);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const x = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${x}s`;
    return `${x}s`;
}

function _updatePausePushUI(pausedUntil) {
    const btn = document.getElementById('btnPausePush');
    const desc = document.getElementById('pushPauseDesc');
    if (!btn || !desc) return;
    if (pausedUntil) {
        btn.textContent = '\u6062\u590d\u63a8\u9001';
        btn.className = 'portal-push-pause-btn resume';
        desc.textContent = `\u63a8\u9001\u5df2\u6682\u505c\uff0c\u5c06\u4e8e ${pausedUntil} \u81ea\u52a8\u6062\u590d`;
        desc.style.color = '#c93400';
    } else {
        btn.textContent = '\u6682\u505c 24 \u5c0f\u65f6';
        btn.className = 'portal-push-pause-btn';
        desc.textContent = '\u6682\u65f6\u5c4f\u853d\u90ae\u4ef6\u901a\u77e5\uff0c\u4e0d\u5f71\u54cd\u8bbe\u7f6e';
        desc.style.color = '';
    }
}

async function togglePausePush() {
    const btn = document.getElementById('btnPausePush');
    const isResuming = btn && btn.classList.contains('resume');
    if (btn) { btn.disabled = true; btn.textContent = '\u5904\u7406\u4e2d...'; }

    if (isResuming) {
        const res = await portalApi('/api/subscriber/session/resume-push', { method: 'POST' });
        if (res.success) {
            _updatePausePushUI(null);
            showPortalToast('\u63a8\u9001\u5df2\u6062\u590d', 'success');
        } else {
            showPortalToast('\u64cd\u4f5c\u5931\u8d25\uff1a' + (res.error || ''), 'error');
        }
    } else {
        const res = await portalApi('/api/subscriber/session/pause-push', {
            method: 'POST',
            body: JSON.stringify({ hours: 24 }),
        });
        if (res.success) {
            _updatePausePushUI(res.paused_until || '');
            showPortalToast('\u63a8\u9001\u5df2\u6682\u505c 24 \u5c0f\u65f6', 'success');
        } else {
            showPortalToast('\u64cd\u4f5c\u5931\u8d25\uff1a' + (res.error || ''), 'error');
        }
    }

    if (btn) btn.disabled = false;
}

// 鈺愨晲鈺愨晲鈺愨晲 Switch Account 鈺愨晲鈺愨晲鈺愨晲
function switchAccount() {
    fetch('/api/session/clear', { method: 'POST' }).finally(() => {
        window.location.href = '/subscribe?force=1';
    });
}

async function logoutCurrentDevice() {
    const btn = document.getElementById('btnLogoutDevice');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '\u9000\u51fa\u4e2d...';
    }
    try {
        await fetch('/api/session/clear', { method: 'POST' });
    } finally {
        window.location.href = '/subscribe?force=1';
    }
}

// 鈺愨晲鈺愨晲鈺愨晲 Unsubscribe 鈺愨晲鈺愨晲鈺愨晲
async function unsubscribe() {
    if (!confirm('\u786e\u5b9a\u8981\u9000\u8ba2\u63a8\u9001\u5417\uff1f\u9000\u8ba2\u540e\u5c06\u4e0d\u518d\u6536\u5230\u4efb\u4f55\u8bfe\u7a0b\u901a\u77e5\u90ae\u4ef6\u3002')) return;

    const btn = document.getElementById('btnUnsubscribe');
    btn.disabled = true;
    btn.textContent = '\u5904\u7406\u4e2d...';

    try {
        const resp = await fetch('/api/unsubscribe', { method: 'POST' });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '\u9000\u8ba2\u5931\u8d25');
        showPortalToast('\u5df2\u6210\u529f\u9000\u8ba2\u63a8\u9001', 'success');
        setTimeout(() => {
            window.location.href = '/subscribe?result=unsubscribed';
        }, 1500);
    } catch (err) {
        btn.disabled = false;
        btn.textContent = '\u9000\u8ba2\u63a8\u9001';
        showPortalToast('\u9000\u8ba2\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5', 'error');
    }
}

// 鈺愨晲鈺愨晲鈺愨晲 Toast 鈺愨晲鈺愨晲鈺愨晲
function showPortalToast(message, type = 'info') {
    const container = document.getElementById('portalToastContainer');
    const toast = document.createElement('div');
    toast.className = `portal-toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = 'portalToastOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// 鈺愨晲鈺愨晲鈺愨晲 Utility 鈺愨晲鈺愨晲鈺愨晲
function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

function csvEscape(text) {
    const s = String(text ?? '');
    return `"${s.replace(/"/g, '""')}"`;
}


