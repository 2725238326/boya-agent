/**
 * 博雅课程推送控制台 - 前端交互逻辑
 */

// ========== 全局状态 ==========
let currentConfig = {};
let searchTimeout = null;
const CONSOLE_TAB_KEY = 'console_active_tab';
let pushLogsTodayOnly = false;

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    const savedTab = localStorage.getItem(CONSOLE_TAB_KEY);
    if (savedTab) switchTab(savedTab);
    loadCourses();
    loadConfig();
    loadStatus();
    loadCategories();

    // 定时刷新
    setInterval(loadStatus, 30000);
    setInterval(loadCourses, 60000);
});

// ========== Tab 切换 ==========
function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));

    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');
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

// ========== API 工具函数 ==========
async function api(url, options = {}) {
    try {
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        return await resp.json();
    } catch (err) {
        console.error('API Error:', err);
        showToast('网络请求失败', 'error');
        return { success: false, error: err.message };
    }
}

// ========== 时间格式化 ==========
function formatInterval(minutes) {
    minutes = parseInt(minutes);
    if (minutes >= 1440) return Math.round(minutes / 1440) + ' 天';
    if (minutes >= 60) {
        const h = Math.floor(minutes / 60);
        const m = minutes % 60;
        return m > 0 ? `${h}h${m}m` : `${h} 小时`;
    }
    return minutes + ' 分钟';
}

// ========== 课程列表 ==========
async function loadCourses() {
    const grid = document.getElementById('courseGrid');
    if (!grid) return;

    const params = new URLSearchParams();
    const keyword = document.getElementById('searchInput').value;
    const category = document.getElementById('categoryFilter').value;
    const campus = document.getElementById('campusFilter').value;
    const selfSign = document.getElementById('selfSignFilter').checked;
    const showExpired = document.getElementById('showExpiredFilter').checked;
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

    if (!result.success || !result.data.length) {
        grid.innerHTML = '<div class="loading">暂无课程数据，点击右上角「立即抓取」开始...</div>';
        return;
    }

    grid.innerHTML = result.data.map(course => renderCourseCard(course)).join('');
}

function renderCourseCard(course) {
    const checkIn = course.check_in_method || course.sign_method || '';
    const signBadge = checkIn.includes('\u81ea\u4e3b')
        ? '<span class="badge badge-self-sign">\u2705 \u81ea\u4e3b\u7b7e\u5230</span>'
        : `<span class="badge badge-not-self-sign">\u2139\ufe0f ${escapeHtml(checkIn || '\u76f4\u63a5\u9009\u8bfe')}</span>`;
    const expiredBadge = course.expired
        ? '<span class="badge badge-full">\u23f3 \u5df2\u8fc7\u671f</span>'
        : '';

    const remaining = course.remaining;
    const capacity = course.capacity || 1;
    const fillPercent = Math.min(100, ((course.enrolled || 0) / capacity) * 100);
    let fillClass = 'green';
    if (remaining <= 0) fillClass = 'red';
    else if (remaining <= 10) fillClass = 'yellow';

    const statusBadge = buildConsoleCourseStatus(course);
    const timingHint = buildConsoleCourseTimingHint(course);

    return `
    <div class="course-card${course.expired ? ' expired' : ''}">
        <div class="card-top">
            <span class="course-name">${escapeHtml(course.name)}</span>
            ${signBadge}
            ${expiredBadge}
        </div>
        ${statusBadge ? `<div class="course-status-row">${statusBadge}</div>` : ''}
        ${timingHint ? `<div class="course-timing-hint">${timingHint}</div>` : ''}
        <div style="margin-bottom:8px;">
            <span class="badge badge-category">${escapeHtml(course.category)}</span>
        </div>
        <div class="course-details">
            <span class="detail-label">\ud83d\udc68\u200d\ud83c\udfeb \u6559\u5e08</span>
            <span>${escapeHtml(course.teacher)}</span>
            <span class="detail-label">\ud83d\udccd \u5730\u70b9</span>
            <span>${escapeHtml(course.location)}</span>
            <span class="detail-label">\ud83c\udfeb \u6821\u533a</span>
            <span>${escapeHtml(course.campus)}</span>
            <span class="detail-label">\u23f0 \u8bfe\u7a0b</span>
            <span>${escapeHtml(course.start_time)} ~ ${escapeHtml(course.end_time)}</span>
            <span class="detail-label">\ud83d\udcdd \u9009\u8bfe</span>
            <span>${escapeHtml(course.enroll_start)} ~ ${escapeHtml(course.enroll_end)}</span>
        </div>
        <div class="capacity-bar">
            <div class="capacity-fill ${fillClass}" style="width:${fillPercent}%"></div>
        </div>
        <div class="capacity-text">
            <span>\u5df2\u9009 ${course.enrolled}/${capacity}</span>
            <span>\u5269\u4f59 ${remaining} \u4eba</span>
        </div>
        ${!course.expired ? `<div style="margin-top:8px;text-align:right;">
            <button class="btn btn-sm btn-accent" onclick="manualPush('${course.id}', this)" style="font-size:12px;">\ud83d\udce4 \u63a8\u9001\u6b64\u8bfe\u7a0b</button>
        </div>` : ''}
    </div>`;
}

function buildConsoleCourseStatus(course) {
    const remaining = Number(course.remaining || 0);
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    const now = new Date();
    const timeValue = enrollStart && !Number.isNaN(enrollStart.getTime()) ? enrollStart.getTime() : null;
    const minutesToStart = timeValue == null ? null : Math.floor((timeValue - now.getTime()) / 60000);

    if (course.expired) {
        return '<span class="course-status-pill muted">\u5df2\u8fc7\u671f</span>';
    }
    if (remaining <= 0) {
        return '<span class="course-status-pill wait">\u5df2\u6ee1\u8e72\u9000\u9009</span>';
    }
    if (minutesToStart !== null && minutesToStart <= 0) {
        return '<span class="course-status-pill hot">\u53ef\u7acb\u5373\u5c1d\u8bd5</span>';
    }
    if (minutesToStart !== null && minutesToStart <= 180) {
        return '<span class="course-status-pill soon">\u5373\u5c06\u5f00\u62a2</span>';
    }
    if (remaining >= 20) {
        return '<span class="course-status-pill easy">\u540d\u989d\u8f83\u5145\u8db3</span>';
    }
    if (remaining <= 5) {
        return '<span class="course-status-pill tight">\u540d\u989d\u7d27\u5f20</span>';
    }
    return '<span class="course-status-pill normal">\u53ef\u52a0\u5165\u5173\u6ce8</span>';
}

function buildConsoleCourseTimingHint(course) {
    const enrollStart = course.enroll_start ? new Date(String(course.enroll_start).replace(' ', 'T')) : null;
    if (!enrollStart || Number.isNaN(enrollStart.getTime()) || course.expired) return '';

    const diffMinutes = Math.floor((enrollStart.getTime() - Date.now()) / 60000);
    if (diffMinutes <= 0) {
        const opened = Math.abs(diffMinutes);
        if (opened < 60) return `\u5df2\u5f00\u9009 ${opened} \u5206\u949f`;
        const hours = Math.floor(opened / 60);
        const mins = opened % 60;
        return mins ? `\u5df2\u5f00\u9009 ${hours} \u5c0f\u65f6 ${mins} \u5206` : `\u5df2\u5f00\u9009 ${hours} \u5c0f\u65f6`;
    }

    if (diffMinutes < 60) return `${diffMinutes} \u5206\u949f\u540e\u5f00\u62a2`;
    if (diffMinutes < 1440) {
        const hours = Math.floor(diffMinutes / 60);
        const mins = diffMinutes % 60;
        return mins ? `${hours} \u5c0f\u65f6 ${mins} \u5206\u540e\u5f00\u62a2` : `${hours} \u5c0f\u65f6\u540e\u5f00\u62a2`;
    }
    const days = Math.floor(diffMinutes / 1440);
    const hours = Math.floor((diffMinutes % 1440) / 60);
    return hours ? `${days} \u5929 ${hours} \u5c0f\u65f6\u540e\u5f00\u62a2` : `${days} \u5929\u540e\u5f00\u62a2`;
}

function debounceSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(loadCourses, 400);
}

function applyTodayNewFilter() {
    const todayNewEl = document.getElementById('todayNewFilter');
    if (todayNewEl) todayNewEl.checked = true;
    const availableNowEl = document.getElementById('availableNowFilter');
    const waitlistEl = document.getElementById('waitlistFilter');
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
    const logTable = document.getElementById('pushLogTable');
    if (logTable) {
        logTable.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
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

// ========== 类别 ==========
async function loadCategories() {
    const result = await api('/api/categories');
    if (!result.success) return;

    const select = document.getElementById('categoryFilter');
    result.data.forEach(cat => {
        const opt = document.createElement('option');
        opt.value = cat;
        opt.textContent = cat;
        select.appendChild(opt);
    });
}

function renderCategoryChips(categories, selectedCategories) {
    const container = document.getElementById('categoryChips');
    container.innerHTML = categories.map(cat => {
        const active = selectedCategories.includes(cat) ? 'active' : '';
        return `<span class="chip ${active}" onclick="toggleChip(this, '${escapeHtml(cat)}')">${escapeHtml(cat)}</span>`;
    }).join('');
}

function toggleChip(el, category) {
    el.classList.toggle('active');
}

function getSelectedChips() {
    const chips = document.querySelectorAll('#categoryChips .chip.active');
    return Array.from(chips).map(c => c.textContent.trim());
}

// ========== 配置 ==========
async function loadConfig() {
    const result = await api('/api/config');
    if (!result.success) return;

    currentConfig = result.data;
    const c = currentConfig;

    // 过滤设置
    document.getElementById('cfgSelfSign').checked = c.self_sign_only;
    const strictEl = document.getElementById('cfgStrictBoya');
    if (strictEl) strictEl.checked = c.strict_boya_only || false;
    document.getElementById('cfgMinRemaining').value = c.min_remaining;
    document.getElementById('minRemainingVal').textContent = c.min_remaining;
    document.getElementById('cfgCampus').value = c.campus_filter || '';

    // 关键词
    renderTags('whitelist', c.keyword_whitelist || []);
    renderTags('blacklist', c.keyword_blacklist || []);

    // 推送
    document.getElementById('cfgTelegram').checked = c.telegram_enabled;
    document.getElementById('cfgEmail').checked = c.email_enabled;
    document.getElementById('cfgRss').checked = c.rss_enabled;
    const dailyEl = document.getElementById('cfgDailySummary');
    if (dailyEl) dailyEl.checked = c.daily_summary_enabled || false;
    const dailyTimeEl = document.getElementById('cfgDailySummaryTime');
    if (dailyTimeEl) dailyTimeEl.value = c.daily_summary_time || '21:00';

    // 间隔
    document.getElementById('cfgInterval').value = c.interval_minutes;
    document.getElementById('intervalVal').textContent = formatInterval(c.interval_minutes);

    // Dashboard
    const dashInterval = document.getElementById('dashInterval');
    if (dashInterval) dashInterval.textContent = c.interval_minutes;

    // 自动选课
    document.getElementById('cfgAutoEnroll').checked = c.auto_enroll_enabled;
    document.getElementById('cfgConfirmEnroll').checked = c.confirm_before_enroll;
    document.getElementById('cfgMaxEnroll').value = c.max_auto_enroll_per_day;
    document.getElementById('maxEnrollVal').textContent = c.max_auto_enroll_per_day;
    renderTags('priority', c.priority_keywords || []);

    // 类别芯片
    const catResult = await api('/api/categories');
    if (catResult.success) {
        renderCategoryChips(catResult.data, c.categories || []);
    }
}

async function saveConfig() {
    const config = {
        categories: getSelectedChips(),
        self_sign_only: document.getElementById('cfgSelfSign').checked,
        strict_boya_only: document.getElementById('cfgStrictBoya')?.checked || false,
        min_remaining: parseInt(document.getElementById('cfgMinRemaining').value),
        campus_filter: document.getElementById('cfgCampus').value,
        keyword_whitelist: getTagValues('whitelist'),
        keyword_blacklist: getTagValues('blacklist'),
        telegram_enabled: document.getElementById('cfgTelegram').checked,
        email_enabled: document.getElementById('cfgEmail').checked,
        rss_enabled: document.getElementById('cfgRss').checked,
        daily_summary_enabled: document.getElementById('cfgDailySummary')?.checked || false,
        daily_summary_time: document.getElementById('cfgDailySummaryTime')?.value || '21:00',
        interval_minutes: parseInt(document.getElementById('cfgInterval').value),
        auto_enroll_enabled: document.getElementById('cfgAutoEnroll').checked,
        priority_keywords: getTagValues('priority'),
        confirm_before_enroll: document.getElementById('cfgConfirmEnroll').checked,
        max_auto_enroll_per_day: parseInt(document.getElementById('cfgMaxEnroll').value),
    };

    const result = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify(config),
    });

    if (result.success) {
        showToast('✅ 配置已保存，将在下次抓取时生效', 'success');
        const status = document.getElementById('saveStatus');
        status.textContent = '✓ 已保存';
        status.classList.add('show');
        setTimeout(() => status.classList.remove('show'), 3000);
        loadConfig(); // refresh dashboard
    } else {
        showToast('❌ 保存失败: ' + result.error, 'error');
    }
}

// ========== Interval Presets ==========
function setInterval(minutes) {
    document.getElementById('cfgInterval').value = minutes;
    document.getElementById('intervalVal').textContent = formatInterval(minutes);

    // Highlight active preset
    document.querySelectorAll('.interval-presets .interval-btn').forEach(btn => {
        const val = parseInt(btn.textContent);
        btn.classList.remove('active');
    });
}

// ========== Interval Quick Edit Modal ==========
function openIntervalDialog() {
    document.getElementById('intervalModal').classList.add('active');
}

function closeIntervalDialog(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('intervalModal').classList.remove('active');
}

async function quickSetInterval(minutes) {
    if (!minutes || minutes < 3) {
        showToast('间隔不能小于 3 分钟', 'error');
        return;
    }
    const result = await api('/api/config', {
        method: 'PUT',
        body: JSON.stringify({ interval_minutes: minutes }),
    });

    if (result.success) {
        showToast(`⏱️ 抓取间隔已改为 ${formatInterval(minutes)}`, 'success');
        closeIntervalDialog({ target: document.getElementById('intervalModal'), currentTarget: document.getElementById('intervalModal') });
        loadConfig();
        loadStatus();
    } else {
        showToast('❌ 修改失败', 'error');
    }
}

// ========== 自动选课 ==========
async function toggleAutoEnroll() {
    const result = await api('/api/enroll/toggle', { method: 'POST' });
    if (result.success) {
        showToast(result.message, 'info');
    }
}

// ========== 标签管理 ==========
function renderTags(type, values) {
    const container = document.getElementById(`${type}Tags`);
    container.innerHTML = values.map((v, i) => `
        <span class="tag" draggable="true" data-index="${i}" data-type="${type}">
            ${escapeHtml(v)}
            <span class="tag-remove" onclick="removeTag('${type}', ${i})">×</span>
        </span>
    `).join('');

    container.dataset.values = JSON.stringify(values);

    if (type === 'priority') {
        initDragSort(container);
    }
}

function addTag(type) {
    const input = document.getElementById(`${type}Input`);
    const value = input.value.trim();
    if (!value) return;

    const container = document.getElementById(`${type}Tags`);
    const values = JSON.parse(container.dataset.values || '[]');

    if (!values.includes(value)) {
        values.push(value);
        renderTags(type, values);
    }

    input.value = '';
    input.focus();
}

function removeTag(type, index) {
    const container = document.getElementById(`${type}Tags`);
    const values = JSON.parse(container.dataset.values || '[]');
    values.splice(index, 1);
    renderTags(type, values);
}

function getTagValues(type) {
    const container = document.getElementById(`${type}Tags`);
    return JSON.parse(container.dataset.values || '[]');
}

// ========== 拖拽排序 ==========
function initDragSort(container) {
    let draggedEl = null;

    container.querySelectorAll('.tag').forEach(tag => {
        tag.addEventListener('dragstart', (e) => {
            draggedEl = tag;
            tag.style.opacity = '0.4';
        });

        tag.addEventListener('dragend', () => {
            tag.style.opacity = '1';
            draggedEl = null;
        });

        tag.addEventListener('dragover', (e) => {
            e.preventDefault();
        });

        tag.addEventListener('drop', (e) => {
            e.preventDefault();
            if (draggedEl === tag) return;

            const values = JSON.parse(container.dataset.values || '[]');
            const fromIdx = parseInt(draggedEl.dataset.index);
            const toIdx = parseInt(tag.dataset.index);

            const [moved] = values.splice(fromIdx, 1);
            values.splice(toIdx, 0, moved);

            renderTags('priority', values);
        });
    });
}

// ========== 系统状态 ==========
async function loadStatus() {
    const result = await api('/api/status');
    if (!result.success) return;

    const d = result.data;

    // Log tab stats
    const el = (id) => document.getElementById(id);
    if (el('statLastRun')) el('statLastRun').textContent = d.last_run || '尚未运行';
    if (el('statRunning')) el('statRunning').textContent = d.is_running ? '运行中...' : '空闲';
    if (el('statTotalRuns')) el('statTotalRuns').textContent = d.total_runs;
    if (el('statNewCourses')) el('statNewCourses').textContent = d.total_new_courses;
    if (el('statPushed')) el('statPushed').textContent = d.total_push_emails ?? d.total_pushed;
    if (el('statDbCourses')) el('statDbCourses').textContent = d.total_courses_in_db || 0;
    if (el('statExpiredCourses')) el('statExpiredCourses').textContent = d.total_expired_courses || 0;
    if (el('statBrowserAlive')) el('statBrowserAlive').textContent = d.browser_alive ? '🟢 存活' : '🔴 离线';
    if (el('statBufferUrgent')) el('statBufferUrgent').textContent = d.push_buffer_urgent || 0;
    if (el('statBufferSoon')) el('statBufferSoon').textContent = d.push_buffer_soon || 0;

    // Dashboard strip
    if (el('dashTotalCourses')) el('dashTotalCourses').textContent = d.total_available_courses ?? 0;
    if (el('dashNewCourses')) el('dashNewCourses').textContent = d.total_new_today ?? 0;
    if (el('dashPushed')) el('dashPushed').textContent = d.total_delivered_today ?? 0;
    if (el('dashExpired')) el('dashExpired').textContent = d.total_expired_courses || 0;
    if (el('dashBrowserStatus')) el('dashBrowserStatus').textContent = d.browser_alive ? '存活' : '离线';
    if (el('dashBrowserIcon')) el('dashBrowserIcon').textContent = d.browser_alive ? '🟢' : '🔴';
    if (el('dashBufferCount')) el('dashBufferCount').textContent = (d.push_buffer_urgent || 0) + (d.push_buffer_soon || 0);
    if (el('dashLastRun')) {
        const t = d.last_run || '未运行';
        el('dashLastRun').textContent = t.length > 10 ? t.slice(11, 16) : t;
    }

    // Header status
    const indicator = document.getElementById('statusIndicator');
    if (!indicator) return;
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');

    if (d.last_error) {
        dot.classList.add('error');
        text.textContent = '错误';
    } else if (d.is_running) {
        dot.classList.remove('error');
        text.textContent = '抓取中...';
    } else {
        dot.classList.remove('error');
        text.textContent = d.last_success ? '运行正常' : '等待首次运行';
    }
}

// ========== 日志 ==========
async function loadPushLogs() {
    const result = await api('/api/logs/push');
    if (!result.success) return;

    const tbody = document.querySelector('#pushLogTable tbody');
    if (!tbody) return;
    if (!result.data.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">暂无送达日志</td></tr>';
        return;
    }
    tbody.innerHTML = result.data.map(log => `
        <tr>
            <td>${escapeHtml(log.pushed_at)}</td>
            <td>${escapeHtml(log.push_type)}</td>
            <td>${escapeHtml(log.course_id)}</td>
            <td class="${log.success ? 'success-badge' : 'fail-badge'}">
                ${log.success ? '✅ 已送达' : '❌ 发送失败'}
            </td>
        </tr>
    `).join('');
}

async function loadEnrollLogs() {
    const result = await api('/api/logs/enroll');
    if (!result.success) return;

    const tbody = document.querySelector('#enrollLogTable tbody');
    if (!tbody) return;
    if (!result.data.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">暂无选课日志</td></tr>';
        return;
    }
    tbody.innerHTML = result.data.map(log => `
        <tr>
            <td>${escapeHtml(log.attempted_at)}</td>
            <td>${escapeHtml(log.course_name)}</td>
            <td class="${log.success ? 'success-badge' : 'fail-badge'}">
                ${log.success ? '✅ 成功' : '❌ 失败'}
            </td>
            <td>${escapeHtml(log.message || '')}</td>
        </tr>
    `).join('');
}

// ========== 手动推送指定课程 ==========
async function manualPush(courseId, btnEl) {
    if (!confirm('确认要手动推送此课程给所有订阅者吗？')) return;
    btnEl.disabled = true;
    btnEl.textContent = '推送中…';

    const result = await api('/api/manual-push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ course_id: courseId }),
    });

    if (result.success) {
        showToast(`发送完成: ${result.message || ''}`, 'success');
        btnEl.textContent = '✓ 已发送';
    } else {
        showToast('发送失败: ' + (result.error || ''), 'error');
        btnEl.disabled = false;
        btnEl.textContent = '📤 推送此课程';
    }
}

// ========== 手动触发 ==========
async function triggerScrape() {
    const btn = document.getElementById('btnTrigger');
    btn.classList.add('loading');
    btn.textContent = '\u6293\u53d6\u4e2d...';

    const result = await api('/api/trigger', { method: 'POST' });
    if (result.success) {
        showToast('\u6293\u53d6\u5b8c\u6210\uff0c\u8bfe\u7a0b\u5217\u8868\u5df2\u5237\u65b0', 'success');
        await loadStatus();
        await loadCourses();
    } else {
        showToast('\u6293\u53d6\u5931\u8d25: ' + (result.error || ''), 'error');
    }

    btn.classList.remove('loading');
    btn.innerHTML = '<span class="btn-icon">\u{1F504}</span> \u7acb\u5373\u6293\u53d6';
}

// ========== Toast 通知 ==========
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'toastOut 0.3s ease forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ========== 用户管理 ==========
let _subscribersAll = [];

async function loadSubscribers() {
    const tbody = document.getElementById('subscribersTbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">加载中…</td></tr>';

    const result = await api('/api/subscribers');
    if (!result.success) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">加载失败</td></tr>';
        return;
    }

    _subscribersAll = result.data || [];

    const total = _subscribersAll.length;
    const active = _subscribersAll.filter(s => s.active && !s.push_is_paused).length;
    const paused = _subscribersAll.filter(s => s.push_is_paused).length;
    const el = (id) => document.getElementById(id);
    if (el('subStatTotal'))  el('subStatTotal').textContent  = total;
    if (el('subStatActive')) el('subStatActive').textContent = active;
    if (el('subStatPaused')) el('subStatPaused').textContent = paused;

    filterSubscribers();
}

function filterSubscribers() {
    const keyword = (document.getElementById('subSearchInput')?.value || '').toLowerCase();
    const status  = document.getElementById('subStatusFilter')?.value || '';
    let list = _subscribersAll;
    if (keyword) list = list.filter(s => s.email.toLowerCase().includes(keyword));
    if (status === 'active')   list = list.filter(s => s.active && !s.push_is_paused);
    else if (status === 'inactive') list = list.filter(s => !s.active);
    else if (status === 'paused')   list = list.filter(s => s.push_is_paused);
    renderSubscribers(list);
}

function renderSubscribers(list) {
    const tbody = document.getElementById('subscribersTbody');
    if (!tbody) return;

    if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted)">无匹配用户</td></tr>';
        return;
    }

    tbody.innerHTML = list.map(s => {
        let accountBadge;
        if (!s.active) accountBadge = '<span class="sub-badge inactive">已停用</span>';
        else if (s.verification_status === 'unverified') accountBadge = '<span class="sub-badge unverified">待验证</span>';
        else accountBadge = '<span class="sub-badge active">已启用</span>';

        const campus   = s.campus_filter ? escapeHtml(s.campus_filter) : '全部';
        const catCount = Array.isArray(s.categories) && s.categories.length ? `${s.categories.length}类` : '全部';
        const selfSign = s.self_sign_only ? '自主签到' : '全部课';
        const pref     = `${campus} · ${catCount} · ${selfSign}`;

        let pushState = '<span class="sub-badge inactive">关闭</span>';
        if (s.active && s.push_is_paused) {
            const until = s.push_paused_until ? `至 ${escapeHtml(s.push_paused_until.slice(5, 16))}` : '已暂停';
            pushState = `<div><span class="sub-badge paused">已暂停</span><div class="sub-meta">${until}</div></div>`;
        } else if (s.active) {
            pushState = '<span class="sub-badge normal">正常发送</span>';
        }

        const delivered7d = s.deliveries_7d != null ? s.deliveries_7d : '—';
        const lastDelivered = s.last_delivered_at ? escapeHtml(s.last_delivered_at.slice(5, 16)) : '—';
        const portalSeen  = s.last_portal_seen_at ? escapeHtml(s.last_portal_seen_at.slice(5, 16)) : '—';
        const regTime     = s.created_at ? escapeHtml(s.created_at.slice(0, 10)) : '—';

        const pauseBtn  = s.push_is_paused
            ? `<button class="sub-op-btn success" onclick="adminClearPause(${s.id})">恢复推送</button>`
            : '';
        const toggleBtn = s.active
            ? `<button class="sub-op-btn danger"  onclick="adminToggleSubscriber(${s.id}, true)">停用</button>`
            : `<button class="sub-op-btn success" onclick="adminToggleSubscriber(${s.id}, false)">启用</button>`;

        return `<tr>
            <td class="sub-email">${escapeHtml(s.email)}</td>
            <td>${accountBadge}</td>
            <td>${pushState}</td>
            <td class="sub-pref">${escapeHtml(pref)}</td>
            <td>${delivered7d}</td>
            <td class="sub-time">${lastDelivered}</td>
            <td class="sub-time">${portalSeen}</td>
            <td class="sub-time">${regTime}</td>
            <td><div class="sub-op-group">${pauseBtn}${toggleBtn}</div></td>
        </tr>`;
    }).join('');
}

async function adminToggleSubscriber(subId, currentActive) {
    const action = currentActive ? '停用' : '启用';
    if (!confirm(`确认要${action}该用户吗？`)) return;
    const result = await api(`/api/admin/subscriber/${subId}/toggle-active`, { method: 'POST' });
    if (result.success) {
        showToast(`✅ 已${action}`, 'success');
        loadSubscribers();
    } else {
        showToast('操作失败: ' + (result.error || ''), 'error');
    }
}

async function adminClearPause(subId) {
    const result = await api(`/api/admin/subscriber/${subId}/clear-pause`, { method: 'POST' });
    if (result.success) {
        showToast('✅ 已恢复推送', 'success');
        loadSubscribers();
    } else {
        showToast('操作失败: ' + (result.error || ''), 'error');
    }
}

// ========== 工具函数 ==========
function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}
