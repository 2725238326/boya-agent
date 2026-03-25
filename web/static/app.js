/**
 * 博雅课程推送后台前端逻辑
 */

let currentConfig = {};
let searchTimeout = null;
let pushLogsTodayOnly = false;
let consoleRefreshWatchToken = 0;
let subscribersAll = [];
let subscriberQuickFilter = 'all';

const CONSOLE_TAB_KEY = 'console_active_tab';

document.addEventListener('DOMContentLoaded', () => {
    const savedTab = localStorage.getItem(CONSOLE_TAB_KEY);
    if (savedTab) switchTab(savedTab);

    loadCourses();
    loadConfig();
    loadStatus();

    window.setInterval(loadStatus, 30000);
    window.setInterval(loadCourses, 60000);
});

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach((tab) => tab.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach((tab) => tab.classList.remove('active'));

    document.querySelector(`[data-tab="${tabName}"]`)?.classList.add('active');
    document.getElementById(`tab-${tabName}`)?.classList.add('active');
    localStorage.setItem(CONSOLE_TAB_KEY, tabName);

    if (tabName === 'logs') {
        loadStatus();
        loadPushLogs();
        loadEnrollLogs();
    }
    if (tabName === 'subscribers') {
        loadSubscribers();
    }
    if (tabName !== 'logs') {
        pushLogsTodayOnly = false;
    }
}

async function api(url, options = {}) {
    const { suppressErrorToast = false, headers = {}, ...fetchOptions } = options;
    try {
        const resp = await fetch(url, {
            headers: {
                Accept: 'application/json',
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
        console.error('Non-JSON API response:', {
            url,
            status: resp.status,
            contentType,
            bodyPreview,
        });
        if (!suppressErrorToast) {
            showToast('接口返回异常页面，请稍后重试', 'error');
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
            showToast('网络请求失败', 'error');
        }
        return { success: false, error: err.message };
    }
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatInterval(minutes) {
    const value = parseInt(minutes, 10);
    if (Number.isNaN(value) || value <= 0) return '-';
    if (value >= 1440) return `${Math.round(value / 1440)} 天`;
    if (value >= 60) {
        const hours = Math.floor(value / 60);
        const mins = value % 60;
        return mins > 0 ? `${hours} 小时 ${mins} 分钟` : `${hours} 小时`;
    }
    return `${value} 分钟`;
}

async function loadCourses() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;

    const params = new URLSearchParams();
    const keyword = document.getElementById('searchInput')?.value || '';
    const category = document.getElementById('categoryFilter')?.value || '';
    const campus = document.getElementById('campusFilter')?.value || '';
    const selfSign = document.getElementById('selfSignFilter')?.checked;
    const showExpired = document.getElementById('showExpiredFilter')?.checked;
    const todayNew = document.getElementById('todayNewFilter')?.checked;
    const availableNow = document.getElementById('availableNowFilter')?.checked;
    const waitlistOnly = document.getElementById('waitlistFilter')?.checked;

    if (keyword) params.set('keyword', keyword);
    if (category) params.set('category', category);
    if (campus) params.set('campus', campus);
    if (selfSign) params.set('self_sign', 'true');
    if (showExpired) params.set('include_expired', 'true');
    if (todayNew) params.set('today_new', 'true');
    if (availableNow) params.set('available_now', 'true');
    if (waitlistOnly) params.set('waitlist_only', 'true');

    const result = await api(`/api/courses?${params.toString()}`);
    const courses = Array.isArray(result.data) ? result.data : [];

    if (!result.success || courses.length === 0) {
        grid.innerHTML = '<div class="loading">暂无课程数据，可以点击右上角“立即抓取”更新一次。</div>';
        return;
    }

    grid.innerHTML = courses.map((course) => renderCourseCard(course)).join('');
}

function isConsoleCourseHot(course) {
    return Boolean(course && course.is_hot_course);
}

function buildConsoleHeatBadge(course) {
    if (!isConsoleCourseHot(course) || course.expired) return '';
    const fillPercent = Math.round(Number(course.fill_percent || 0));
    const detail = course.hot_reason === 'remaining'
        ? `剩余 ${Number(course.remaining || 0)} 人`
        : `热度 ${fillPercent}%`;
    return `<span class="badge badge-hot-watch">热点盯盘 · ${detail}</span>`;
}

function renderCourseCard(course) {
    const checkIn = getConsoleCourseCheckInLabel(course);
    const signBadge = checkIn.includes('自主')
        ? '<span class="badge badge-self-sign">✅ 自主签到</span>'
        : `<span class="badge badge-not-self-sign">ℹ️ ${escapeHtml(checkIn)}</span>`;
    const hotBadge = buildConsoleHeatBadge(course);
    const expiredBadge = course.expired
        ? '<span class="badge badge-full">⏳ 已过期</span>'
        : '';

    const remaining = Number(course.remaining || 0);
    const capacity = Number(course.capacity || 1);
    const fillPercent = Math.min(100, (Number(course.enrolled || 0) / capacity) * 100);
    let fillClass = 'green';
    if (remaining <= 0) fillClass = 'red';
    else if (remaining <= 10) fillClass = 'yellow';

    const statusBadge = buildConsoleCourseStatus(course);
    const timingHint = buildConsoleCourseTimingHint(course);

    return `
    <div class="course-card${course.expired ? ' expired' : ''}">
        <div class="card-top">
            <span class="course-name">${escapeHtml(course.name)}</span>
            ${hotBadge}
            ${signBadge}
            ${expiredBadge}
        </div>
        ${statusBadge ? `<div class="course-status-row">${statusBadge}</div>` : ''}
        ${timingHint ? `<div class="course-timing-hint">${timingHint}</div>` : ''}
        <div style="margin-bottom:8px;">
            <span class="badge badge-category">${escapeHtml(course.category)}</span>
        </div>
        <div class="course-details">
            <span class="detail-label">👨‍🏫 教师</span>
            <span>${escapeHtml(course.teacher)}</span>
            <span class="detail-label">📍 地点</span>
            <span>${escapeHtml(course.location)}</span>
            <span class="detail-label">🏫 校区</span>
            <span>${escapeHtml(course.campus)}</span>
            <span class="detail-label">⏰ 课程</span>
            <span>${escapeHtml(course.start_time)} ~ ${escapeHtml(course.end_time)}</span>
            <span class="detail-label">📝 选课</span>
            <span>${escapeHtml(course.enroll_start)} ~ ${escapeHtml(course.enroll_end)}</span>
        </div>
        <div class="capacity-bar">
            <div class="capacity-fill ${fillClass}" style="width:${fillPercent}%"></div>
        </div>
        <div class="capacity-text">
            <span>已选 ${Number(course.enrolled || 0)}/${capacity}</span>
            <span>剩余 ${remaining} 人</span>
        </div>
        ${!course.expired ? `<div style="margin-top:8px;text-align:right;">
            <button class="btn btn-sm btn-accent" onclick="manualPush('${course.id}', this)" style="font-size:12px;">📤 推送此课程</button>
        </div>` : ''}
    </div>`;
}

function getConsoleCourseCheckInLabel(course) {
    const displayLabel = String(course.display_check_in_method || '').trim();
    if (displayLabel) return displayLabel;

    const rawCheckIn = String(course.check_in_method || '').trim();
    const rawSignMethod = String(course.sign_method || '').trim();
    if (`${rawCheckIn} ${rawSignMethod}`.includes('自主')) {
        return '自主签到';
    }
    return '常规签到';
}

function buildConsoleCourseStatus(course) {
    const remaining = Number(course.remaining || 0);
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    const timeValue = enrollStart && !Number.isNaN(enrollStart.getTime()) ? enrollStart.getTime() : null;
    const minutesToStart = timeValue == null ? null : Math.floor((timeValue - Date.now()) / 60000);
    const isHot = isConsoleCourseHot(course);

    if (course.expired) return '<span class="course-status-pill muted">已过期</span>';
    if (remaining <= 0) return `<span class="course-status-pill wait">${isHot ? '已满高频盯盘' : '已满可蹲退选'}</span>`;
    if (minutesToStart !== null && minutesToStart <= 0) return `<span class="course-status-pill hot">${isHot ? '热点可立即尝试' : '可立即尝试'}</span>`;
    if (isHot) return '<span class="course-status-pill hot">热点重点巡检</span>';
    if (minutesToStart !== null && minutesToStart <= 180) return '<span class="course-status-pill soon">即将开抢</span>';
    if (remaining >= 20) return '<span class="course-status-pill easy">名额较充足</span>';
    if (remaining <= 5) return '<span class="course-status-pill tight">名额紧张</span>';
    return '<span class="course-status-pill normal">可加入关注</span>';
}

function buildConsoleCourseTimingHint(course) {
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    if (!enrollStart || Number.isNaN(enrollStart.getTime()) || course.expired) return '';

    const diffMinutes = Math.floor((enrollStart.getTime() - Date.now()) / 60000);
    if (diffMinutes <= 0) {
        const opened = Math.abs(diffMinutes);
        if (opened < 60) return `已开选 ${opened} 分钟`;
        const hours = Math.floor(opened / 60);
        const mins = opened % 60;
        return mins ? `已开选 ${hours} 小时 ${mins} 分` : `已开选 ${hours} 小时`;
    }

    if (diffMinutes < 60) return `${diffMinutes} 分钟后开抢`;
    if (diffMinutes < 1440) {
        const hours = Math.floor(diffMinutes / 60);
        const mins = diffMinutes % 60;
        return mins ? `${hours} 小时 ${mins} 分后开抢` : `${hours} 小时后开抢`;
    }

    const days = Math.floor(diffMinutes / 1440);
    const hours = Math.floor((diffMinutes % 1440) / 60);
    return hours ? `${days} 天 ${hours} 小时后开抢` : `${days} 天后开抢`;
}

function debounceSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = window.setTimeout(loadCourses, 400);
}

function applyTodayNewFilter() {
    const todayNewEl = document.getElementById('todayNewFilter');
    const availableNowEl = document.getElementById('availableNowFilter');
    const waitlistEl = document.getElementById('waitlistFilter');

    if (todayNewEl) todayNewEl.checked = true;
    if (availableNowEl) availableNowEl.checked = false;
    if (waitlistEl) waitlistEl.checked = false;

    switchTab('courses');
    loadCourses();
}

function applyAvailableNowFilter() {
    const todayNewEl = document.getElementById('todayNewFilter');
    const availableNowEl = document.getElementById('availableNowFilter');
    const waitlistEl = document.getElementById('waitlistFilter');

    if (todayNewEl) todayNewEl.checked = false;
    if (availableNowEl) availableNowEl.checked = true;
    if (waitlistEl) waitlistEl.checked = false;

    switchTab('courses');
    loadCourses();
}

function applyTodayDeliveredFilter() {
    pushLogsTodayOnly = true;
    switchTab('logs');
    loadPushLogs();
    document.getElementById('pushLogTable')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function toggleConsoleQuickFilter(mode) {
    const availableNowEl = document.getElementById('availableNowFilter');
    const waitlistEl = document.getElementById('waitlistFilter');
    if (!availableNowEl || !waitlistEl) {
        loadCourses();
        return;
    }

    if (mode === 'available' && availableNowEl.checked) {
        waitlistEl.checked = false;
    }
    if (mode === 'waitlist' && waitlistEl.checked) {
        availableNowEl.checked = false;
    }

    loadCourses();
}

async function loadCategories() {
    const result = await api('/api/categories');
    if (!result.success) return [];

    const categories = Array.isArray(result.data) ? result.data : [];
    const select = document.getElementById('categoryFilter');
    if (select) {
        const currentValue = select.value;
        select.innerHTML = '<option value="">全部类别</option>';
        categories.forEach((category) => {
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            select.appendChild(option);
        });
        if (currentValue) select.value = currentValue;
    }

    renderCategoryChips(categories, currentConfig.categories || []);
    return categories;
}

function renderCategoryChips(categories, selectedCategories) {
    const container = document.getElementById('categoryChips');
    if (!container) return;
    container.innerHTML = categories.map((category) => {
        const active = selectedCategories.includes(category) ? 'active' : '';
        return `<span class="chip ${active}" onclick="toggleChip(this, '${escapeHtml(category)}')">${escapeHtml(category)}</span>`;
    }).join('');
}

function toggleChip(element) {
    element.classList.toggle('active');
}

function getSelectedChips() {
    return Array.from(document.querySelectorAll('#categoryChips .chip.active'))
        .map((chip) => chip.textContent.trim());
}

async function loadConfig() {
    const result = await api('/api/config');
    if (!result.success) return;

    currentConfig = result.data || {};
    const config = currentConfig;

    document.getElementById('cfgSelfSign').checked = !!config.self_sign_only;
    document.getElementById('cfgStrictBoya').checked = !!config.strict_boya_only;
    document.getElementById('cfgMinRemaining').value = config.min_remaining ?? 1;
    document.getElementById('minRemainingVal').textContent = config.min_remaining ?? 1;
    document.getElementById('cfgCampus').value = config.campus_filter || '';

    renderTags('whitelist', config.keyword_whitelist || []);
    renderTags('blacklist', config.keyword_blacklist || []);

    document.getElementById('cfgTelegram').checked = !!config.telegram_enabled;
    document.getElementById('cfgEmail').checked = !!config.email_enabled;
    document.getElementById('cfgRss').checked = !!config.rss_enabled;
    document.getElementById('cfgDailySummary').checked = !!config.daily_summary_enabled;
    document.getElementById('cfgDailySummaryTime').value = config.daily_summary_time || '21:00';

    setScrapeIntervalPreset(config.interval_minutes ?? 10);
    document.getElementById('dashInterval').textContent = config.interval_minutes ?? '-';

    document.getElementById('cfgAutoEnroll').checked = !!config.auto_enroll_enabled;
    document.getElementById('cfgConfirmEnroll').checked = !!config.confirm_before_enroll;
    document.getElementById('cfgMaxEnroll').value = config.max_auto_enroll_per_day ?? 2;
    document.getElementById('maxEnrollVal').textContent = config.max_auto_enroll_per_day ?? 2;
    renderTags('priority', config.priority_keywords || []);

    await loadCategories();
}

async function saveConfig() {
    const payload = {
        categories: getSelectedChips(),
        self_sign_only: document.getElementById('cfgSelfSign').checked,
        strict_boya_only: document.getElementById('cfgStrictBoya').checked,
        min_remaining: parseInt(document.getElementById('cfgMinRemaining').value, 10),
        campus_filter: document.getElementById('cfgCampus').value,
        keyword_whitelist: getTagValues('whitelist'),
        keyword_blacklist: getTagValues('blacklist'),
        telegram_enabled: document.getElementById('cfgTelegram').checked,
        email_enabled: document.getElementById('cfgEmail').checked,
        rss_enabled: document.getElementById('cfgRss').checked,
        daily_summary_enabled: document.getElementById('cfgDailySummary').checked,
        daily_summary_time: document.getElementById('cfgDailySummaryTime').value || '21:00',
        interval_minutes: parseInt(document.getElementById('cfgInterval').value, 10),
        auto_enroll_enabled: document.getElementById('cfgAutoEnroll').checked,
        priority_keywords: getTagValues('priority'),
        confirm_before_enroll: document.getElementById('cfgConfirmEnroll').checked,
        max_auto_enroll_per_day: parseInt(document.getElementById('cfgMaxEnroll').value, 10),
    };

    const result = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify(payload),
    });

    const status = document.getElementById('saveStatus');
    if (result.success) {
        showToast('配置已保存，将在下一次抓取时生效', 'success');
        if (status) {
            status.textContent = '已保存';
            status.classList.add('show');
            window.setTimeout(() => status.classList.remove('show'), 3000);
        }
        loadConfig();
        return;
    }

    showToast(`保存失败: ${result.error || '未知错误'}`, 'error');
    if (status) {
        status.textContent = '保存失败';
        status.classList.add('show');
        window.setTimeout(() => status.classList.remove('show'), 3000);
    }
}

function setScrapeIntervalPreset(minutes) {
    document.getElementById('cfgInterval').value = minutes;
    document.getElementById('intervalVal').textContent = formatInterval(minutes);
    document.querySelectorAll('.interval-presets .interval-btn').forEach((button) => {
        button.classList.toggle('active', parseInt(button.dataset.minutes || '0', 10) === minutes);
    });
}

function openIntervalDialog() {
    document.getElementById('intervalModal')?.classList.add('active');
}

function closeIntervalDialog(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('intervalModal')?.classList.remove('active');
}

async function quickSetInterval(minutes) {
    const value = parseInt(minutes, 10);
    if (!value || value < 3) {
        showToast('抓取间隔不能小于 3 分钟', 'error');
        return;
    }

    const result = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify({ interval_minutes: value }),
    });

    if (result.success) {
        showToast(`抓取间隔已改为 ${formatInterval(value)}`, 'success');
        closeIntervalDialog({
            target: document.getElementById('intervalModal'),
            currentTarget: document.getElementById('intervalModal'),
        });
        loadConfig();
        loadStatus();
        return;
    }

    showToast(`修改失败: ${result.error || '未知错误'}`, 'error');
}

async function toggleAutoEnroll() {
    const result = await api('/api/enroll/toggle', { method: 'POST' });
    if (result.success) {
        showToast(result.message || '自动选课状态已更新', 'info');
    } else {
        showToast(`操作失败: ${result.error || '未知错误'}`, 'error');
        loadConfig();
    }
}

function renderTags(type, values) {
    const container = document.getElementById(`${type}Tags`);
    if (!container) return;

    container.innerHTML = values.map((value, index) => `
        <span class="tag" draggable="true" data-index="${index}" data-type="${type}">
            ${escapeHtml(value)}
            <span class="tag-remove" onclick="removeTag('${type}', ${index})">×</span>
        </span>
    `).join('');
    container.dataset.values = JSON.stringify(values);

    if (type === 'priority') {
        initDragSort(container);
    }
}

function addTag(type) {
    const input = document.getElementById(`${type}Input`);
    if (!input) return;

    const value = input.value.trim();
    if (!value) return;

    const container = document.getElementById(`${type}Tags`);
    const values = JSON.parse(container?.dataset.values || '[]');
    if (!values.includes(value)) {
        values.push(value);
        renderTags(type, values);
    }

    input.value = '';
    input.focus();
}

function removeTag(type, index) {
    const container = document.getElementById(`${type}Tags`);
    const values = JSON.parse(container?.dataset.values || '[]');
    values.splice(index, 1);
    renderTags(type, values);
}

function getTagValues(type) {
    const container = document.getElementById(`${type}Tags`);
    return JSON.parse(container?.dataset.values || '[]');
}

function initDragSort(container) {
    let draggedElement = null;

    container.querySelectorAll('.tag').forEach((tag) => {
        tag.addEventListener('dragstart', () => {
            draggedElement = tag;
            tag.style.opacity = '0.4';
        });

        tag.addEventListener('dragend', () => {
            tag.style.opacity = '1';
            draggedElement = null;
        });

        tag.addEventListener('dragover', (event) => {
            event.preventDefault();
        });

        tag.addEventListener('drop', (event) => {
            event.preventDefault();
            if (!draggedElement || draggedElement === tag) return;

            const values = JSON.parse(container.dataset.values || '[]');
            const fromIndex = parseInt(draggedElement.dataset.index, 10);
            const toIndex = parseInt(tag.dataset.index, 10);

            const [moved] = values.splice(fromIndex, 1);
            values.splice(toIndex, 0, moved);
            renderTags('priority', values);
        });
    });
}

async function loadStatus() {
    const result = await api('/api/status');
    if (!result.success) return;

    const data = result.data || {};
    const el = (id) => document.getElementById(id);

    if (el('statLastRun')) el('statLastRun').textContent = data.last_run || '尚未运行';
    if (el('statRunning')) el('statRunning').textContent = data.is_running ? '运行中...' : '空闲';
    if (el('statTotalRuns')) el('statTotalRuns').textContent = data.total_runs ?? 0;
    if (el('statNewCourses')) el('statNewCourses').textContent = data.total_new_courses ?? 0;
    if (el('statPushed')) el('statPushed').textContent = data.total_push_emails ?? data.total_pushed ?? 0;
    if (el('statDbCourses')) el('statDbCourses').textContent = data.total_courses_in_db ?? 0;
    if (el('statExpiredCourses')) el('statExpiredCourses').textContent = data.total_expired_courses ?? 0;
    if (el('statBrowserAlive')) el('statBrowserAlive').textContent = data.browser_alive ? '🟢 存活' : '🔴 离线';
    if (el('statBufferUrgent')) el('statBufferUrgent').textContent = data.push_buffer_urgent ?? 0;
    if (el('statBufferSoon')) el('statBufferSoon').textContent = data.push_buffer_soon ?? 0;

    if (el('dashTotalCourses')) el('dashTotalCourses').textContent = data.total_available_courses ?? 0;
    if (el('dashNewCourses')) el('dashNewCourses').textContent = data.total_new_today ?? 0;
    if (el('dashPushed')) el('dashPushed').textContent = data.total_delivered_today ?? 0;
    if (el('dashExpired')) el('dashExpired').textContent = data.total_expired_courses ?? 0;
    if (el('dashBrowserStatus')) el('dashBrowserStatus').textContent = data.browser_alive ? '存活' : '离线';
    if (el('dashBrowserIcon')) el('dashBrowserIcon').textContent = data.browser_alive ? '🟢' : '🔴';
    if (el('dashBufferCount')) el('dashBufferCount').textContent = (data.push_buffer_urgent || 0) + (data.push_buffer_soon || 0);
    if (el('dashLastRun')) {
        const text = data.last_run || '未运行';
        el('dashLastRun').textContent = text.length > 10 ? text.slice(11, 16) : text;
    }

    const indicator = document.getElementById('statusIndicator');
    const dot = indicator?.querySelector('.status-dot');
    const text = indicator?.querySelector('.status-text');
    if (!indicator || !dot || !text) return;

    if (data.last_error) {
        dot.classList.add('error');
        text.textContent = '错误';
    } else if (data.is_running) {
        dot.classList.remove('error');
        text.textContent = '抓取中...';
    } else {
        dot.classList.remove('error');
        text.textContent = data.last_success ? '运行正常' : '等待首次运行';
    }
}

async function loadPushLogs() {
    const result = await api('/api/logs/push');
    if (!result.success) return;

    const tbody = document.querySelector('#pushLogTable tbody');
    const hint = document.getElementById('pushLogHint');
    if (!tbody) return;

    let logs = Array.isArray(result.data) ? result.data : [];
    if (pushLogsTodayOnly) {
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        logs = logs.filter((log) => String(log.pushed_at || '').startsWith(today));
        if (hint) hint.textContent = '当前只显示今天的送达记录';
    } else if (hint) {
        hint.textContent = '显示最近送达记录';
    }

    if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">暂无送达日志</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map((log) => `
        <tr>
            <td>${escapeHtml(log.pushed_at)}</td>
            <td>${escapeHtml(log.push_type)}</td>
            <td>${escapeHtml(log.course_id)}</td>
            <td class="${log.success ? 'success-badge' : 'fail-badge'}">
                ${log.success ? '已送达' : '发送失败'}
            </td>
        </tr>
    `).join('');
}

async function loadEnrollLogs() {
    const result = await api('/api/logs/enroll');
    if (!result.success) return;

    const tbody = document.querySelector('#enrollLogTable tbody');
    if (!tbody) return;

    const logs = Array.isArray(result.data) ? result.data : [];
    if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">暂无选课日志</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map((log) => `
        <tr>
            <td>${escapeHtml(log.attempted_at)}</td>
            <td>${escapeHtml(log.course_name)}</td>
            <td class="${log.success ? 'success-badge' : 'fail-badge'}">
                ${log.success ? '成功' : '失败'}
            </td>
            <td>${escapeHtml(log.message || '')}</td>
        </tr>
    `).join('');
}

async function manualPush(courseId, button) {
    if (!window.confirm('确认要把这门课程手动推送给所有订阅用户吗？')) return;

    button.disabled = true;
    button.textContent = '推送中...';

    const result = await api('/api/manual-push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ course_id: courseId }),
    });

    if (result.success) {
        showToast(`发送完成 ${result.message || ''}`.trim(), 'success');
        button.textContent = '已发送';
        return;
    }

    showToast(`发送失败: ${result.error || '未知错误'}`, 'error');
    button.disabled = false;
    button.textContent = '📤 推送此课程';
}

async function triggerScrape() {
    const button = document.getElementById('btnTrigger');
    if (!button) return;

    const originalHtml = button.innerHTML;
    const requestedAt = Date.now();
    button.classList.add('loading');
    button.disabled = true;
    button.textContent = '抓取中...';

    const result = await api('/api/trigger', {
        method: 'POST',
        suppressErrorToast: true,
    });

    if (result.success) {
        if (result.joined_existing) {
            showToast(result.message || '后台已有抓取任务，正在为你同步最新结果', 'info');
        } else {
            showToast(result.message || '已开始后台抓取课程，稍后自动刷新', 'info');
        }
        const watchToken = ++consoleRefreshWatchToken;
        void waitForConsoleRefresh(requestedAt, watchToken);
    } else {
        showToast(`抓取失败: ${result.error || '未知错误'}`, 'error');
    }

    button.disabled = false;
    button.classList.remove('loading');
    button.innerHTML = originalHtml;
}

async function waitForConsoleRefresh(requestedAt, watchToken) {
    const deadline = Date.now() + 180000;
    const threshold = requestedAt - 5000;

    while (Date.now() < deadline) {
        await sleep(2500);
        if (watchToken !== consoleRefreshWatchToken) return;

        const statusResult = await api('/api/status', { suppressErrorToast: true });
        if (!statusResult.success) continue;

        const data = statusResult.data || {};
        if (data.is_running) continue;

        const lastSuccess = data.last_success
            ? new Date(String(data.last_success).replace(' ', 'T')).getTime()
            : 0;
        const lastRun = data.last_run
            ? new Date(String(data.last_run).replace(' ', 'T')).getTime()
            : 0;

        if ((lastSuccess && lastSuccess >= threshold) || (lastRun && lastRun >= threshold)) {
            await loadStatus();
            await loadCourses();
            showToast('抓取完成，课程列表已刷新', 'success');
            return;
        }
    }

    if (watchToken === consoleRefreshWatchToken) {
        showToast('后台仍在抓取，完成后列表会自动显示最新结果', 'info');
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    window.setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s ease forwards';
        window.setTimeout(() => toast.remove(), 300);
    }, 4000);
}

async function loadSubscribers() {
    const listEl = document.getElementById('subscribersList');
    if (!listEl) return;

    listEl.innerHTML = '<div class="sub-empty-state">正在加载用户列表...</div>';
    const result = await api('/api/subscribers');

    if (!result.success) {
        listEl.innerHTML = '<div class="sub-empty-state">用户列表加载失败，请稍后再试。</div>';
        return;
    }

    subscribersAll = Array.isArray(result.data) ? result.data : [];
    renderSubscriberSummary(result.summary || {});
    populateSubscriberCampusOptions(subscribersAll);

    filterSubscribers();
}

function renderSubscriberSummary(summary) {
    const total = summary.total ?? subscribersAll.length;
    const activeSending = summary.active_sending ?? subscribersAll.filter((item) => item.active && !item.push_is_paused).length;
    const paused = summary.paused ?? subscribersAll.filter((item) => item.push_is_paused).length;
    const unverified = summary.unverified ?? subscribersAll.filter((item) => item.verification_status === 'unverified').length;
    const dormant = summary.dormant ?? subscribersAll.filter((item) => item.is_dormant).length;
    const joined = summary.joined_7d ?? subscribersAll.filter((item) => item.joined_recently).length;

    document.getElementById('subStatTotal').textContent = total;
    document.getElementById('subStatActive').textContent = activeSending;
    document.getElementById('subStatPaused').textContent = paused;
    document.getElementById('subStatUnverified').textContent = unverified;
    document.getElementById('subStatDormant').textContent = dormant;
    document.getElementById('subStatJoined').textContent = joined;
}

function populateSubscriberCampusOptions(list) {
    const select = document.getElementById('subCampusFilter');
    if (!select) return;

    const previous = select.value || '';
    const campuses = Array.from(new Set(
        list
            .map((item) => String(item.campus_filter || '').trim())
            .filter(Boolean)
    )).sort((a, b) => a.localeCompare(b, 'zh-CN'));

    select.innerHTML = '<option value="">全部校区</option>' + campuses
        .map((campus) => `<option value="${escapeHtml(campus)}">${escapeHtml(campus)}</option>`)
        .join('');

    if (campuses.includes(previous)) {
        select.value = previous;
    }
}

function applySubscriberQuickFilter(mode) {
    subscriberQuickFilter = mode;
    document.querySelectorAll('.sub-summary-card').forEach((card) => card.classList.remove('active'));
    const activeCard = {
        all: 'subSummaryAll',
        active: 'subSummarySending',
        paused: 'subSummaryPaused',
        unverified: 'subSummaryUnverified',
        dormant: 'subSummaryDormant',
        joined_7d: 'subSummaryJoined',
    }[mode];
    if (activeCard) {
        document.getElementById(activeCard)?.classList.add('active');
    }
    filterSubscribers();
}

function resetSubscriberFilters() {
    document.getElementById('subSearchInput').value = '';
    document.getElementById('subStatusFilter').value = '';
    document.getElementById('subCampusFilter').value = '';
    document.getElementById('subSortFilter').value = 'created_desc';
    applySubscriberQuickFilter('all');
}

function filterSubscribers() {
    const keyword = (document.getElementById('subSearchInput')?.value || '').toLowerCase();
    const status = document.getElementById('subStatusFilter')?.value || '';
    const campus = document.getElementById('subCampusFilter')?.value || '';
    const sort = document.getElementById('subSortFilter')?.value || 'created_desc';

    let list = [...subscribersAll];
    if (keyword) {
        list = list.filter((item) => {
            const haystack = [
                item.email || '',
                item.campus_filter || '',
                Array.isArray(item.categories) ? item.categories.join(' ') : '',
                item.self_sign_only ? '自主签到' : '全部课程',
            ].join(' ').toLowerCase();
            return haystack.includes(keyword);
        });
    }
    if (campus) {
        list = list.filter((item) => String(item.campus_filter || '') === campus);
    }

    if (subscriberQuickFilter && subscriberQuickFilter !== 'all') {
        list = list.filter((item) => matchesSubscriberFilter(item, subscriberQuickFilter));
    }
    if (status) {
        list = list.filter((item) => matchesSubscriberFilter(item, status));
    }

    list.sort((a, b) => getSubscriberSortValue(a, sort, b));

    renderSubscribers(list);
    const meta = document.getElementById('subResultsMeta');
    if (meta) {
        meta.textContent = `当前显示 ${list.length} / ${subscribersAll.length} 位用户`;
    }
}

function matchesSubscriberFilter(item, mode) {
    if (mode === 'active') return item.active && item.verified && !item.push_is_paused;
    if (mode === 'inactive') return !item.active;
    if (mode === 'paused') return Boolean(item.push_is_paused);
    if (mode === 'unverified') return item.verification_status === 'unverified' || !item.verified;
    if (mode === 'dormant') return Boolean(item.is_dormant);
    if (mode === 'joined_7d') return Boolean(item.joined_recently);
    return true;
}

function getSubscriberSortValue(a, mode, b) {
    const asTime = (value) => {
        if (!value) return 0;
        const parsed = new Date(String(value).replace(' ', 'T')).getTime();
        return Number.isNaN(parsed) ? 0 : parsed;
    };

    if (mode === 'recent_seen') {
        return asTime(b.last_portal_seen_at) - asTime(a.last_portal_seen_at);
    }
    if (mode === 'deliveries_desc') {
        return (b.deliveries_7d || 0) - (a.deliveries_7d || 0);
    }
    if (mode === 'reminders_desc') {
        return (b.pending_reminders || 0) - (a.pending_reminders || 0);
    }
    return asTime(b.created_at) - asTime(a.created_at);
}

function renderSubscribers(list) {
    const listEl = document.getElementById('subscribersList');
    if (!listEl) return;

    if (list.length === 0) {
        listEl.innerHTML = '<div class="sub-empty-state">没有符合当前筛选条件的用户。</div>';
        return;
    }

    listEl.innerHTML = list.map((subscriber) => {
        const statusBadges = [];
        if (!subscriber.active) {
            statusBadges.push('<span class="sub-badge inactive">已停用</span>');
        } else if (subscriber.verification_status === 'unverified' || !subscriber.verified) {
            statusBadges.push('<span class="sub-badge unverified">待验证</span>');
        } else {
            statusBadges.push('<span class="sub-badge active">已启用</span>');
        }

        if (subscriber.push_is_paused) {
            statusBadges.push('<span class="sub-badge paused">推送已暂停</span>');
        } else if (subscriber.active) {
            statusBadges.push('<span class="sub-badge normal">正常推送</span>');
        }

        if (subscriber.is_dormant) {
            statusBadges.push('<span class="sub-badge neutral">沉默用户</span>');
        }
        if (subscriber.joined_recently) {
            statusBadges.push('<span class="sub-badge accent">近期新增</span>');
        }

        const campus = subscriber.campus_filter ? escapeHtml(subscriber.campus_filter) : '全部校区';
        const categories = Array.isArray(subscriber.categories) && subscriber.categories.length
            ? `${subscriber.categories.length} 类`
            : '全部类别';
        const selfSign = subscriber.self_sign_only ? '仅自主签到' : '全部课程';
        const delivered7d = subscriber.deliveries_7d ?? 0;
        const pendingReminders = subscriber.pending_reminders ?? 0;
        const lastDelivered = formatShortDateTime(subscriber.last_delivered_at) || '暂无';
        const portalSeen = formatShortDateTime(subscriber.last_portal_seen_at) || '未访问';
        const createdAt = formatDateOnly(subscriber.created_at) || '未知';
        const pauseUntil = formatShortDateTime(subscriber.push_paused_until);

        const actionButtons = [];
        if (subscriber.active && subscriber.verified && !subscriber.push_is_paused) {
            actionButtons.push(`<button class="sub-op-btn" onclick="adminPauseSubscriber(${subscriber.id}, 24)">暂停 24h</button>`);
        }
        if (subscriber.push_is_paused) {
            actionButtons.push(`<button class="sub-op-btn success" onclick="adminClearPause(${subscriber.id})">恢复推送</button>`);
        }
        actionButtons.push(
            subscriber.active
                ? `<button class="sub-op-btn danger" onclick="adminToggleSubscriber(${subscriber.id}, true)">停用</button>`
                : `<button class="sub-op-btn success" onclick="adminToggleSubscriber(${subscriber.id}, false)">启用</button>`
        );

        return `
        <article class="subscriber-card${!subscriber.active ? ' is-inactive' : ''}">
            <div class="subscriber-card-head">
                <div class="subscriber-identity">
                    <div class="sub-email">${escapeHtml(subscriber.email)}</div>
                    <div class="sub-joined">注册于 ${createdAt}</div>
                </div>
                <div class="subscriber-badges">${statusBadges.join('')}</div>
            </div>
            <div class="subscriber-card-body">
                <div class="subscriber-panel">
                    <span class="subscriber-panel-label">偏好</span>
                    <div class="subscriber-panel-value">${campus}</div>
                    <div class="subscriber-panel-meta">${categories} / ${selfSign}</div>
                </div>
                <div class="subscriber-panel">
                    <span class="subscriber-panel-label">活跃度</span>
                    <div class="subscriber-panel-value">${portalSeen}</div>
                    <div class="subscriber-panel-meta">最近访问</div>
                </div>
                <div class="subscriber-panel">
                    <span class="subscriber-panel-label">送达与提醒</span>
                    <div class="subscriber-panel-value">${delivered7d} / ${pendingReminders}</div>
                    <div class="subscriber-panel-meta">近 7 天送达 / 待提醒</div>
                </div>
                <div class="subscriber-panel">
                    <span class="subscriber-panel-label">最近送达</span>
                    <div class="subscriber-panel-value">${lastDelivered}</div>
                    <div class="subscriber-panel-meta">${pauseUntil ? `暂停至 ${pauseUntil}` : '未暂停'}</div>
                </div>
            </div>
            <div class="subscriber-card-actions">
                ${actionButtons.join('')}
            </div>
        </article>`;
    }).join('');
}

function formatShortDateTime(value) {
    if (!value) return '';
    const normalized = String(value).trim();
    if (!normalized) return '';
    return normalized.length >= 16 ? normalized.slice(5, 16) : normalized;
}

function formatDateOnly(value) {
    if (!value) return '';
    return String(value).trim().slice(0, 10);
}

async function sendServiceUpdateBroadcast() {
    const confirmed = window.confirm(
        '确认要向所有已验证且仍激活的用户发送“站点入口调整通知”邮件吗？'
    );
    if (!confirmed) return;

    showToast('正在发送站点调整通知，请稍候...', 'info');
    const result = await api('/api/admin/broadcast/service-update', {
        method: 'POST',
    });

    if (result.success) {
        showToast(result.message || '站点调整通知已发送', 'success');
        return;
    }

    showToast(`发送失败: ${result.error || '未知错误'}`, 'error');
}

async function adminToggleSubscriber(subscriberId, currentActive) {
    const action = currentActive ? '停用' : '启用';
    if (!window.confirm(`确认要${action}这个用户吗？`)) return;

    const result = await api(`/api/admin/subscriber/${subscriberId}/toggle-active`, { method: 'POST' });
    if (result.success) {
        showToast(`已${action}`, 'success');
        loadSubscribers();
        return;
    }

    showToast(`操作失败: ${result.error || '未知错误'}`, 'error');
}

async function adminClearPause(subscriberId) {
    const result = await api(`/api/admin/subscriber/${subscriberId}/clear-pause`, { method: 'POST' });
    if (result.success) {
        showToast('已恢复推送', 'success');
        loadSubscribers();
        return;
    }

    showToast(`操作失败: ${result.error || '未知错误'}`, 'error');
}

async function adminPauseSubscriber(subscriberId, hours = 24) {
    const result = await api(`/api/admin/subscriber/${subscriberId}/pause-push`, {
        method: 'POST',
        body: JSON.stringify({ hours }),
    });
    if (result.success) {
        showToast(result.message || '已暂停推送', 'success');
        loadSubscribers();
        return;
    }

    showToast(`操作失败: ${result.error || '未知错误'}`, 'error');
}

function escapeHtml(text) {
    if (text == null) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, (char) => map[char]);
}
