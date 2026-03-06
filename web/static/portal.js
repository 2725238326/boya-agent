/**
 * 博雅课程用户门户 — 交互逻辑
 */

// ══════ State ══════
let portalState = {
    email: '',
    subscriber: null,
    reminderCourseIds: new Set(),
    notifications: [],
    filteredNotifications: [],
    notificationsHours: 24,
};

// ══════ Init ══════
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const email = params.get('email') || '';
    portalState.email = email;
    document.getElementById('userEmail').textContent = email || '加载中…';
    initTabs();
    loadPortalData();
});

// ══════ API Helper ══════
async function portalApi(url, options = {}) {
    try {
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        return await resp.json();
    } catch (err) {
        console.error('API Error:', err);
        showPortalToast('网络请求失败', 'error');
        return { success: false, error: err.message };
    }
}

// ══════ Data Loading ══════
async function loadPortalData() {
    const sessionRes = await portalApi('/api/subscriber/session');
    if (!sessionRes.success) {
        window.location.href = '/subscribe';
        return;
    }
    portalState.subscriber = sessionRes.data;

    // Load in parallel
    const [coursesRes, remindersRes, categoriesRes] = await Promise.all([
        portalApi('/api/courses'),
        portalApi('/api/subscriber/session/reminders'),
        portalApi('/api/categories'),
    ]);

    document.getElementById('userEmail').textContent = portalState.subscriber.email || '已登录';
    renderSettings(portalState.subscriber, categoriesRes.success ? categoriesRes.data : []);

    // Courses
    if (coursesRes.success) {
        const activeCourses = coursesRes.data.filter(c => !c.expired);
        const availableCourses = activeCourses.filter(c => c.remaining > 0);
        document.getElementById('heroCount').textContent = availableCourses.length;
        renderCourses(coursesRes.data);
    }

    // Reminders
    if (remindersRes.success) {
        portalState.reminderCourseIds = new Set(remindersRes.data.map(r => r.course_id));
        renderReminders(remindersRes.data);
        // Update reminder count badge
        const pendingCount = remindersRes.data.filter(r => !r.sent).length;
        const badge = document.getElementById('reminderBadge');
        if (pendingCount > 0) {
            badge.textContent = pendingCount;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }

    await reloadNotifications();
}

// ══════ Tabs ══════
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
}

// ══════ Course Rendering ══════
function renderCourses(courses) {
    const grid = document.getElementById('courseGrid');
    if (!courses || !courses.length) {
        grid.innerHTML = `
            <div class="portal-empty" style="grid-column: 1/-1;">
                <div class="portal-empty-icon">📭</div>
                <div class="portal-empty-text">暂无课程</div>
                <div class="portal-empty-hint">等待系统抓取新课程</div>
            </div>`;
        return;
    }

    // 分为有名额 / 已满
    const available = courses.filter(c => c.remaining > 0 || c.expired);
    const full = courses.filter(c => c.remaining <= 0 && !c.expired);

    let html = '';

    // 有名额的课程
    if (available.length > 0) {
        html += available.map(c => renderCourseCard(c)).join('');
    } else {
        html += `<div class="portal-empty" style="grid-column:1/-1;">
            <div class="portal-empty-icon">🎉</div>
            <div class="portal-empty-text">所有课程都已满员</div>
            <div class="portal-empty-hint">有退课名额时系统会立即通知你</div>
        </div>`;
    }

    grid.innerHTML = html;

    // 已满课程折叠区
    let fullSection = document.getElementById('fullCoursesSection');
    if (full.length > 0) {
        if (!fullSection) {
            fullSection = document.createElement('div');
            fullSection.id = 'fullCoursesSection';
            grid.parentNode.insertBefore(fullSection, grid.nextSibling);
        }
        fullSection.innerHTML = `
            <div class="portal-full-toggle" onclick="toggleFullCourses()">
                <span class="portal-full-toggle-icon" id="fullToggleIcon">▶</span>
                <span>已满课程</span>
                <span class="portal-full-count">${full.length}</span>
                <span class="portal-full-hint">有退课时系统自动即时推送</span>
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
    icon.textContent = isHidden ? '▼' : '▶';
}

function renderCourseCard(course, isFull = false) {
    const checkIn = course.check_in_method || course.sign_method || '';
    const isSelf = checkIn.includes('自主');
    const signBadge = isSelf
        ? '<span class="portal-badge portal-badge-self">✓ 自主签到</span>'
        : `<span class="portal-badge portal-badge-regular">${escapeHtml(checkIn || '常规签到')}</span>`;

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
            ? `<button class="portal-btn-remind reminded" disabled>✓ 已设提醒</button>`
            : `<button class="portal-btn-remind" onclick="registerReminder('${escapeHtml(course.id)}', this)">🔔 提醒我选课</button>`;

    const cardClass = isFull ? 'portal-course-card portal-card-full' : 'portal-course-card';
    const fullBadge = (remaining <= 0 && !course.expired) ? '<span class="portal-badge portal-badge-full">已满</span>' : '';

    return `
    <div class="${cardClass}">
        <div class="portal-card-top">
            <span class="portal-course-name">${escapeHtml(course.name)}</span>
            ${fullBadge}
            ${signBadge}
        </div>
        <div class="portal-card-meta">
            <span class="portal-badge portal-badge-category">${escapeHtml(course.category)}</span>
            &nbsp;${escapeHtml(course.teacher)} · ${escapeHtml(course.campus)}
        </div>
        <div class="portal-card-details">
            <span class="portal-detail-label">📍 地点</span>
            <span>${escapeHtml(course.location)}</span>
            <span class="portal-detail-label">⏰ 课程</span>
            <span>${escapeHtml(course.start_time)} ~ ${escapeHtml(course.end_time)}</span>
            <span class="portal-detail-label">📝 选课</span>
            <span>${escapeHtml(course.enroll_start)} ~ ${escapeHtml(course.enroll_end)}</span>
        </div>
        <div class="portal-capacity-bar">
            <div class="portal-capacity-fill ${fillClass}" style="width:${fillPct}%"></div>
        </div>
        <div class="portal-capacity-text">
            <span>已选 ${course.enrolled}/${capacity}</span>
            <span>剩余 ${remaining} 人</span>
        </div>
        ${remindBtn ? `<div class="portal-card-actions">${remindBtn}</div>` : ''}
    </div>`;
}

// ══════ Course Filtering ══════
let courseSearchTimeout = null;

function filterCourses() {
    clearTimeout(courseSearchTimeout);
    courseSearchTimeout = setTimeout(async () => {
        const params = new URLSearchParams();
        const keyword = document.getElementById('portalSearch').value;
        const campus = document.getElementById('portalCampus').value;
        const selfSign = document.getElementById('portalSelfSign').checked;
        const showExpired = document.getElementById('portalExpired').checked;

        if (keyword) params.set('keyword', keyword);
        if (campus) params.set('campus', campus);
        if (selfSign) params.set('self_sign', 'true');
        if (showExpired) params.set('include_expired', 'true');

        const res = await portalApi(`/api/courses?${params.toString()}`);
        if (res.success) {
            renderCourses(res.data);
        }
    }, 350);
}

// ══════ Register Reminder ══════
async function registerReminder(courseId, btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = '注册中…';

    const res = await portalApi(`/api/remind/${courseId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    });

    if (res.success) {
        portalState.reminderCourseIds.add(courseId);
        btnEl.textContent = '✓ 已设提醒';
        btnEl.classList.add('reminded');
        showPortalToast('选课提醒已注册，开始前 5 分钟通知你', 'success');

        // Refresh reminders
        const remRes = await portalApi('/api/subscriber/session/reminders');
        if (remRes.success) renderReminders(remRes.data);
    } else {
        btnEl.disabled = false;
        btnEl.textContent = '🔔 提醒我选课';
        showPortalToast('注册失败：' + (res.error || ''), 'error');
    }
}

// ══════ Settings Rendering ══════
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
    btn.textContent = '保存中…';

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
        showPortalToast('偏好设置已保存', 'success');
        portalState.subscriber = res.data;
    } else {
        showPortalToast('保存失败：' + (res.error || ''), 'error');
    }

    btn.disabled = false;
    btn.textContent = '保存设置';
}

// ══════ Reminders Rendering ══════
function renderReminders(reminders) {
    const container = document.getElementById('reminderList');
    if (!reminders || !reminders.length) {
        container.innerHTML = `
            <div class="portal-empty">
                <div class="portal-empty-icon">🔔</div>
                <div class="portal-empty-text">暂无选课提醒</div>
                <div class="portal-empty-hint">在课程页面点击「提醒我选课」注册提醒</div>
            </div>`;
        return;
    }
    container.innerHTML = reminders.map(r => `
        <div class="portal-reminder-item">
            <div class="portal-reminder-info">
                <div class="portal-reminder-name">${escapeHtml(r.course_name)}</div>
                <div class="portal-reminder-meta">
                    ${escapeHtml(r.course_category)} · ${escapeHtml(r.course_teacher)}
                    · 选课开始 ${escapeHtml(r.enroll_start)}
                </div>
            </div>
            <span class="portal-reminder-status ${r.sent ? 'sent' : 'pending'}">
                ${r.sent ? '✓ 已通知' : '等待中'}
            </span>
        </div>
    `).join('');
}

// ══════ Notification Center ══════
function renderNotifications(notifications) {
    const container = document.getElementById('notificationTimeline');
    if (!notifications || !notifications.length) {
        container.innerHTML = `
            <div class="portal-empty">
                <div class="portal-empty-icon">📭</div>
                <div class="portal-empty-text">最近 ${portalState.notificationsHours} 小时暂无推送记录</div>
                <div class="portal-empty-hint">系统发送通知后会在这里显示时间线</div>
            </div>`;
        return;
    }

    container.innerHTML = notifications.map(item => {
        const typeClass = item.event_type === 'snipe' ? 'snipe' : 'new';
        const typeText = item.event_type === 'snipe' ? '退课补录' : '新发';
        const statusText = item.success ? '已送达' : '发送失败';
        const statusClass = item.success ? 'success' : 'failed';
        return `
        <div class="portal-notify-item">
            <div class="portal-notify-main">
                <div class="portal-notify-title">${escapeHtml(item.course_name || '未知课程')}</div>
                <div class="portal-notify-meta">
                    ${escapeHtml(item.course_category || '未分类')} · ${escapeHtml(item.channel || 'email')} · ${escapeHtml(item.sent_at || '')}
                </div>
            </div>
            <div class="portal-notify-badges">
                <span class="portal-notify-type ${typeClass}">${typeText}</span>
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
        showPortalToast('没有可导出的通知记录', 'error');
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

// ══════ Switch Account ══════
function switchAccount() {
    fetch('/api/session/clear', { method: 'POST' }).finally(() => {
        window.location.href = '/subscribe?force=1';
    });
}

async function logoutCurrentDevice() {
    const btn = document.getElementById('btnLogoutDevice');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '退出中…';
    }
    try {
        await fetch('/api/session/clear', { method: 'POST' });
    } finally {
        window.location.href = '/subscribe?force=1';
    }
}

// ══════ Unsubscribe ══════
async function unsubscribe() {
    if (!confirm('确定要退订推送吗？退订后将不再收到任何课程通知邮件。')) return;

    const btn = document.getElementById('btnUnsubscribe');
    btn.disabled = true;
    btn.textContent = '处理中…';

    try {
        const resp = await fetch('/api/unsubscribe', { method: 'POST' });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '退订失败');
        showPortalToast('已成功退订推送', 'success');
        setTimeout(() => {
            window.location.href = '/subscribe?result=unsubscribed';
        }, 1500);
    } catch (err) {
        btn.disabled = false;
        btn.textContent = '退订推送';
        showPortalToast('退订失败，请稍后重试', 'error');
    }
}

// ══════ Toast ══════
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

// ══════ Utility ══════
function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

function csvEscape(text) {
    const s = String(text ?? '');
    return `"${s.replace(/"/g, '""')}"`;
}
