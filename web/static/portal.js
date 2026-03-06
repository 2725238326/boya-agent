/**
 * 博雅课程用户门户 — 交互逻辑
 */

// ══════ State ══════
let portalState = {
    token: '',
    email: '',
    subscriber: null,
    reminderCourseIds: new Set(),
};

// ══════ Init ══════
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token') || localStorage.getItem('portal_token') || '';
    const email = params.get('email') || localStorage.getItem('portal_email') || '';

    if (!token) {
        window.location.href = '/subscribe';
        return;
    }

    portalState.token = token;
    portalState.email = email;
    localStorage.setItem('portal_token', token);
    if (email) localStorage.setItem('portal_email', email);

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
    // Load in parallel
    const [coursesRes, remindersRes, categoriesRes] = await Promise.all([
        portalApi('/api/courses'),
        portalApi(`/api/subscriber/${portalState.token}/reminders`),
        portalApi('/api/categories'),
    ]);

    // Load subscriber info
    if (portalState.email) {
        const subRes = await portalApi('/api/subscriber/lookup', {
            method: 'POST',
            body: JSON.stringify({ email: portalState.email }),
        });
        if (subRes.success) {
            portalState.subscriber = subRes.data;
            document.getElementById('userEmail').textContent = subRes.data.email;
            renderSettings(subRes.data, categoriesRes.success ? categoriesRes.data : []);
        }
    }

    // Courses
    if (coursesRes.success) {
        const activeCourses = coursesRes.data.filter(c => !c.expired);
        document.getElementById('heroCount').textContent = activeCourses.length;
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
        }
    }
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
    grid.innerHTML = courses.map(c => renderCourseCard(c)).join('');
}

function renderCourseCard(course) {
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
    const remindBtn = course.expired
        ? ''
        : isReminded
            ? `<button class="portal-btn-remind reminded" disabled>✓ 已设提醒</button>`
            : `<button class="portal-btn-remind" onclick="registerReminder('${escapeHtml(course.id)}', this)">🔔 提醒我选课</button>`;

    return `
    <div class="portal-course-card">
        <div class="portal-card-top">
            <span class="portal-course-name">${escapeHtml(course.name)}</span>
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
    if (!portalState.token) return;
    btnEl.disabled = true;
    btnEl.textContent = '注册中…';

    const res = await portalApi(`/api/remind/${portalState.token}/${courseId}`, {
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
        const remRes = await portalApi(`/api/subscriber/${portalState.token}/reminders`);
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

    const res = await portalApi(`/api/subscriber/${portalState.token}`, {
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

// ══════ Switch Account ══════
function switchAccount() {
    localStorage.removeItem('portal_token');
    localStorage.removeItem('portal_email');
    window.location.href = '/subscribe';
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
