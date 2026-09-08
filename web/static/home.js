async function fetchJson(url) {
    const resp = await fetch(url, {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(12000),
    });

    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    if (!contentType.includes('application/json')) {
        throw new Error(`Non-JSON response: ${resp.status}`);
    }

    const data = await resp.json();
    if (!resp.ok || !data.success) {
        throw new Error(data.error || `Request failed: ${resp.status}`);
    }
    return data;
}

async function loadHomeInsights() {
    const availableEl = document.getElementById('availableCount');
    const activeEl = document.getElementById('activeCount');
    const nextNameEl = document.getElementById('nextEnrollName');
    const nextHintEl = document.getElementById('nextEnrollHint');
    const generatedAtEl = document.getElementById('homeGeneratedAt');
    if (!availableEl || !activeEl || !nextNameEl || !nextHintEl || !generatedAtEl) {
        return;
    }

    try {
        const data = await fetchJson('/api/public/insights');
        const payload = data.data || {};

        availableEl.textContent = payload.available_count ?? '-';
        activeEl.textContent = payload.active_count ?? '-';
        generatedAtEl.textContent = '报名时间以官方为准';

        if (payload.next_enroll) {
            nextNameEl.textContent = payload.next_enroll.course_name || '即将开抢';
            nextHintEl.textContent = formatCountdown(payload.next_enroll.seconds_left || 0);
        } else {
            nextNameEl.textContent = '暂无即将开抢课程';
            nextHintEl.textContent = '有新课程进入开抢窗口后，这里会自动更新。';
        }
    } catch (err) {
        generatedAtEl.textContent = '暂不可用';
        nextNameEl.textContent = '数据加载失败';
        nextHintEl.textContent = '请稍后刷新首页重试。';
    }
}

async function loadHomeSession() {
    const portalBtn = document.getElementById('portalEntryBtn');
    if (!(portalBtn instanceof HTMLAnchorElement)) return;

    try {
        const data = await fetchJson('/api/subscriber/session');
        if (!data.data || !data.data.email) return;
        portalBtn.textContent = '我的门户';
        portalBtn.href = '/portal';
    } catch (err) {
        // Logged-out users can keep the default portal entry link.
    }
}

function formatCountdown(secondsLeft) {
    const total = Math.max(0, Number(secondsLeft || 0));
    if (total < 60) return '1 分钟内开抢';
    if (total < 3600) return `${Math.floor(total / 60)} 分钟后开抢`;

    if (total < 86400) {
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        return minutes ? `${hours} 小时 ${minutes} 分后开抢` : `${hours} 小时后开抢`;
    }

    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    return hours ? `${days} 天 ${hours} 小时后开抢` : `${days} 天后开抢`;
}

document.addEventListener('DOMContentLoaded', () => {
    loadHomeInsights();
    loadHomeSession();
    loadPublicCourses();
    document.getElementById('courseFilters')?.addEventListener('submit', event => event.preventDefault());
    document.getElementById('courseFilters')?.addEventListener('input', renderPublicCourses);
    document.getElementById('courseFilters')?.addEventListener('reset', () => setTimeout(renderPublicCourses, 0));
    document.getElementById('reloadCourses')?.addEventListener('click', loadPublicCourses);
});

/** @type {Array<Record<string, any>>} */
let publicCourses = [];
let publicCoursesLoading = false;
let publicCoursesLoaded = false;

function homeNode(tag, text, className = '') {
    const element = document.createElement(tag);
    element.textContent = String(text ?? '');
    element.className = className;
    return element;
}

function homeFilterValue(id) {
    const element = document.getElementById(id);
    return element instanceof HTMLInputElement || element instanceof HTMLSelectElement ? element.value : '';
}

function courseLabel(course) {
    if (course.expired) return '已结束';
    if (course.remaining <= 0) return '已满';
    return course.enrollment_open ? '可报名' : '未开选';
}

async function loadPublicCourses() {
    if (publicCoursesLoading) return;
    const grid = document.getElementById('publicCourseGrid');
    const summary = document.getElementById('courseSummary');
    const source = document.getElementById('courseSource');
    const button = document.getElementById('reloadCourses');
    if (!grid || !summary || !source) return;
    publicCoursesLoading = true;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    grid.setAttribute('aria-busy', 'true');
    summary.textContent = '正在加载课程…';
    try {
        const response = await fetchJson('/api/courses');
        publicCourses = Array.isArray(response.data) ? response.data : [];
        publicCoursesLoaded = true;
        const status = response.source || {};
        source.textContent = status.last_success
            ? `最近成功采集：${status.last_success}（北京时间）。${status.degraded ? '最近采集异常，当前展示已有记录。' : ''}名额以官方选课页为准。`
            : '暂无法确认最近成功采集时间，名额请以官方选课页为准。';
        renderPublicCourses();
    } catch {
        publicCoursesLoaded = false;
        publicCourses = [];
        grid.replaceChildren();
        summary.textContent = '课程加载失败，请点击“重新加载列表”重试。';
        source.textContent = '本次未取得课程数据，不能据此判断是否有课。';
    } finally {
        publicCoursesLoading = false;
        grid.setAttribute('aria-busy', 'false');
        if (button instanceof HTMLButtonElement) button.disabled = false;
    }
}

function renderPublicCourses() {
    if (!publicCoursesLoaded) return;
    const grid = document.getElementById('publicCourseGrid');
    const summary = document.getElementById('courseSummary');
    if (!grid || !summary) return;
    const keyword = homeFilterValue('courseSearch').trim().toLowerCase();
    const campus = homeFilterValue('courseCampus');
    const state = homeFilterValue('courseState');
    const labels = { open: '可报名', upcoming: '未开选', full: '已满' };
    const courses = publicCourses.filter(course =>
        String(course.name || '').toLowerCase().includes(keyword)
        && String(course.campus || '').includes(campus)
        && (!state || courseLabel(course) === labels[state]));
    summary.textContent = courses.length ? `显示 ${courses.length} 门课程（在最近 ${publicCourses.length} 条记录中筛选，最多读取 200 条）。`
        : publicCourses.length ? '没有匹配的课程，可清除筛选。' : '当前列表暂无课程，可稍后重新加载或查看官方选课页。';
    const fragment = document.createDocumentFragment();
    for (const course of courses) {
        const card = homeNode('article', '', 'home-card home-course');
        card.append(homeNode('p', courseLabel(course), 'home-course-status'));
        card.append(homeNode('h3', course.name));
        card.append(homeNode('p', `剩余 ${Math.max(0, Number(course.remaining) || 0)} 个名额`, 'home-course-seats'));
        card.append(homeNode('p', `报名：${course.enroll_start || '时间待确认'} — ${course.enroll_end || '时间待确认'}`));
        card.append(homeNode('p', `${course.campus || '校区待确认'} · ${course.category || '类别待确认'}`));
        const details = document.createElement('details');
        details.append(homeNode('summary', '课程详情'));
        details.append(homeNode('p', `上课：${course.start_time || '待确认'} — ${course.end_time || '待确认'}`));
        details.append(homeNode('p', `教师：${course.teacher || '待确认'}；地点：${course.location || '待确认'}`));
        details.append(homeNode('p', `签到：${course.display_check_in_method || '待确认'}`));
        card.append(details);
        const official = document.createElement('a');
        official.href = 'https://bykc.buaa.edu.cn/';
        official.target = '_blank';
        official.rel = 'noopener';
        official.className = 'home-card-link';
        official.textContent = '前往官方选课';
        card.append(official);
        const personal = document.createElement('a');
        personal.href = '/portal';
        personal.className = 'home-card-link home-personal-link';
        personal.textContent = '管理个人提醒';
        card.append(personal);
        fragment.append(card);
    }
    grid.replaceChildren(fragment);
}
