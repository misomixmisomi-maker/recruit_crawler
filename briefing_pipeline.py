"""Saramin + JobKorea public listings for a downstream, evidence-based briefing.

No email, credentials, personal profile, or invented commute/fit scores are stored.
HTML layouts and blocked responses are reported, never treated as empty results.
"""
import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from saramin_crawler import SaraminCrawler

KEYWORDS = ['콘텐츠 디자이너', '브랜드 디자이너', '커머스 디자이너',
            'AI 디자이너', 'AI 크리에이터', '숏폼 디자이너']
SEOUL = re.compile(r'서울(?:특별시)?\s*([가-힣]+구)?')
ENTRY = re.compile(r'신입|경력\s*무관')
OTHER_ROLE = re.compile(r'백엔드|프론트엔드|엔지니어|간호사|보험설계|의류\s*생산|니트\s*디자이너')
ROLE = re.compile(r'디자이너|디자인|크리에이터|영상\s*(?:제작|편집)|룩북|모션그래픽')
BLOCK_MARKERS = ['HTTP_B', 'captcha', '접근이 제한', '비정상적인 접근', 'Access Denied']


def stamp():
    return datetime.now(ZoneInfo('Asia/Seoul')).isoformat()


def plain(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'nav', 'footer', 'header']):
        node.decompose()
    return soup.get_text(' ', strip=True)


class PublicClient:
    def __init__(self):
        self.last_request = 0.0
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'RecruitBriefing/1.0 (public job listing reader)'

    def get(self, url, **kwargs):
        time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.get(url, timeout=(8, 20), **kwargs)
        if response.status_code != 200:
            raise ValueError(f'HTTP {response.status_code}: {url}')
        # Error redirects or explicit challenges must not be parsed or bypassed.
        if '/error/' in response.url or any(x.lower() in response.text[:10000].lower() for x in BLOCK_MARKERS):
            raise ValueError(f'접근 제한 또는 확인 페이지: {response.url}')
        return response


def record(source, job_id, url, title, company='', location='', career='', deadline='', **extra):
    return dict(source=source, id=str(job_id), url=url, title=title, company=company,
                location=location, career=career, deadline=deadline,
                keywords=[], listing_checked_at=stamp(), detail_checked_at=None,
                detail_status='not_checked', detail_text='', jobposting=None,
                **extra)


def parse_saramin(html):
    soup = BeautifulSoup(html, 'html.parser')
    parser = SaraminCrawler()
    rows = []
    for item in soup.select('div.item_recruit'):
        data = parser.extract_job_info_from_api(item, '')
        if not data or not data.get('title') or not data.get('link'):
            raise ValueError('사람인 필수 필드 파싱 실패')
        job_id = parse_qs(urlparse(data['link']).query).get('rec_idx', [data.get('rec_idx', '')])[0]
        if not job_id:
            raise ValueError('사람인 공고 ID 없음')
        # Store a canonical URL without tracking parameters.
        rows.append(record('saramin', job_id,
                           f'https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={job_id}',
                           data['title'], data.get('company') or '', data.get('location') or '',
                           data.get('career') or '', data.get('deadline') or '',
                           education=data.get('education') or '', work_type=data.get('work_type') or ''))
    return rows


def json_postings(soup):
    def walk(value):
        if isinstance(value, dict):
            kind = value.get('@type', '')
            if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
                yield value
            for v in value.values():
                if isinstance(v, (dict, list)):
                    yield from walk(v)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue


def parse_jobkorea(html):
    """Support structured JobPosting data and linked legacy/mobile cards.

    Unknown metadata stays unknown. No assumption that a search hit is entry-level.
    """
    soup = BeautifulSoup(html, 'html.parser')
    rows = {}
    for posting in json_postings(soup):
        url = urljoin('https://www.jobkorea.co.kr', posting.get('url') or '')
        match = re.search(r'/GI_Read/(\d+)', url, re.I)
        if not match:
            continue
        org = posting.get('hiringOrganization') or {}
        org = org if isinstance(org, dict) else {}
        job = record('jobkorea', match[1], f'https://www.jobkorea.co.kr/Recruit/GI_Read/{match[1]}',
                     posting.get('title') or '', org.get('name') or '',
                     deadline=posting.get('validThrough') or '')
        job['jobposting'] = posting
        rows[job['id']] = job
    for link in soup.select('a[href]'):
        href = link.get('href', '')
        match = re.search(r'/GI_Read/(\d+)', href, re.I)
        if not match:
            continue
        job_id = match[1]
        title = link.get('title') or link.get_text(' ', strip=True)
        if not title or len(title) < 4 or title in ['즉시 지원', '입사지원', '스크랩']:
            continue
        card = link.find_parent(attrs={'data-sentry-component': 'CardJob'})
        if card is not None and link.get('data-sentry-component') != 'Title':
            continue
        if card is None:
            card = link.find_parent(['article', 'li'])
        if card is None:
            card = link.find_parent(class_=re.compile(r'list-post|recruit-info|list-item|listItem', re.I))
        text = card.get_text(' ', strip=True) if card else ''
        company_node = card.select_one('.name, .corp-name, .corpName, .company-name') if card else None
        if company_node is None and card:
            # Current CardJob: company is the text link in the span after Title.
            heading = card.select_one('a[data-sentry-component="Title"]')
            if heading and heading.parent:
                sibling = heading.parent.find_next_sibling('span')
                company_node = sibling.select_one('a') if sibling else None
        company = company_node.get_text(' ', strip=True) if company_node else ''
        metadata = text.replace(title, '').replace(company, '') if company else text.replace(title, '')
        loc = re.search(r'(?:서울(?:특별시)?|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)(?:\s*[가-힣]+[구시군])?', metadata)
        career = re.search(r'경력\s*무관|신입\s*[·/&]\s*경력(?:\s*\d+\s*년\s*[↑이상]*)?|신입|경력\s*\d+\s*년\s*[↑이상]*', metadata)
        deadline = re.search(r'\d{4}\.\d{1,2}\.\d{1,2}|~\s*\d{1,2}/\d{1,2}|상시채용|채용시\s*마감|오늘마감|내일마감', metadata)
        job = record('jobkorea', job_id, f'https://www.jobkorea.co.kr/Recruit/GI_Read/{job_id}',
                     title, company, loc.group(0) if loc else '', career.group(0) if career else '',
                     deadline.group(0) if deadline else '', listing_excerpt=text[:3000])
        # Prefer descriptive title links over image or company links.
        if job_id not in rows or len(title) > len(rows[job_id]['title']):
            rows[job_id] = job
    return list(rows.values())


def collect_source(client, source, keywords, pages):
    jobs, errors, requests_ok = {}, [], 0
    for keyword in keywords:
        try:
            for page in range(1, pages + 1):
                print(f'{source}: {keyword} page {page}', flush=True)
                if source == 'saramin':
                    response = client.get('https://www.saramin.co.kr/zf_user/search/get-recruit-list',
                        params=dict(searchType='search', recruitPage=page, recruitSort='relation',
                                    recruitPageCount=40, searchword=keyword, search_optional_item='y',
                                    search_done='y', panel_count='y', preview='y', mainSearch='n'))
                    data = response.json()
                    if 'innerHTML' not in data:
                        raise ValueError('사람인 응답 구조 변경: innerHTML 없음')
                    rows = parse_saramin(data['innerHTML'])
                    count = int(str(data.get('count', '0')).replace(',', ''))
                    if not rows and count > 0:
                        raise ValueError('사람인 검색 건수와 파싱 결과 불일치')
                else:
                    response = client.get('https://www.jobkorea.co.kr/Search/',
                                          params={'stext': keyword, 'Page_No': page})
                    rows = parse_jobkorea(response.text)
                    # A changed or client-rendered layout is not a verified zero.
                    if not rows:
                        raise ValueError('잡코리아 목록 파싱 불가: 동적 화면·구조 변경 또는 결과 미확인')
                requests_ok += 1
                for row in rows:
                    if row['id'] in jobs:
                        if keyword not in jobs[row['id']]['keywords']:
                            jobs[row['id']]['keywords'].append(keyword)
                    else:
                        row['keywords'] = [keyword]
                        jobs[row['id']] = row
                if len(rows) < (40 if source == 'saramin' else 25):
                    break
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(dict(keyword=keyword, error=str(exc), checked_at=stamp()))
            # Do not repeatedly probe a blocked or unparseable site.
            break
    return list(jobs.values()), dict(source=source, collected=len(jobs), requests_ok=requests_ok,
                                   status='partial' if errors and jobs else 'error' if errors else 'ok',
                                   errors=errors)


def triage(job):
    location, career, title = (job.get(x) or '' for x in ('location', 'career', 'title'))
    if OTHER_ROLE.search(title) or not ROLE.search(title):
        return 'excluded', '대상 디자인 직무와 다름'
    if re.search(r'채용\s*마감|접수\s*마감|모집\s*종료', job.get('deadline', '')):
        return 'excluded', '마감 표시'
    if location and location != '지역 없음' and not SEOUL.search(location):
        return 'excluded', '서울 외 근무지'
    if not ENTRY.search(career) and re.search(r'경력|\d+\s*년', career) and career != '경력 없음':
        return 'excluded', '경력 필수'
    if re.search(r'경력\s*\d+\s*년|\d+\s*[~～-]\s*\d+\s*년차', title):
        return 'conditional', '제목의 경력 요건과 검색목록 조건 대조 필요'
    return 'conditional', '상세 접수상태·신입 조건·실제 근무지·업무 유사성 검증 필요'


def check_detail(client, job):
    try:
        response = client.get(job['url'])
        soup = BeautifulSoup(response.text, 'html.parser')
        postings = list(json_postings(soup))
        # Match title before trusting structured data from an unrelated recommendation.
        posting = next((p for p in postings if (p.get('title') or '').strip() == job['title'].strip()), None)
        if posting is None and len(postings) == 1:
            p = postings[0]
            purl = str(p.get('url') or '')
            if job['id'] in purl:
                posting = p
        root = soup.select_one('.jv_cont, .recruit-contents, .recruit-info, main, #content') or soup.body or soup
        text = plain(str(root))
        if len(text) < 100:
            raise ValueError('상세 본문 부족 또는 화면 로딩 미완료')
        job['detail_text'] = text[:20000]
        job['detail_status'] = 'text_available'
        job['jobposting'] = posting
        if posting:
            job['detail_status'] = 'structured_available'
            job['deadline'] = posting.get('validThrough') or job['deadline']
            desc = plain(posting.get('description') or '')
            if desc:
                job['detail_text'] = desc[:20000]
            valid = posting.get('validThrough')
            if valid:
                try:
                    deadline = datetime.fromisoformat(valid.replace('Z', '+00:00'))
                    if deadline.tzinfo is None:
                        deadline = deadline.replace(tzinfo=ZoneInfo('Asia/Seoul'))
                    if deadline < datetime.now(timezone.utc):
                        job['category'], job['reason'] = 'excluded', '구조화된 마감일 경과'
                except ValueError:
                    pass
        # Text is evidence for review, not an automatic judgment of all essential qualifications.
    except (requests.RequestException, ValueError, TypeError) as exc:
        job['detail_status'] = 'error'
        job['detail_error'] = str(exc)
    job['detail_checked_at'] = stamp()


def export_bundle(jobs, sources, output):
    output.mkdir(parents=True, exist_ok=True)
    visible = [j for j in jobs if j['category'] != 'excluded']
    # Unverified candidates cannot be sorted by fabricated travel durations or scores.
    bundle = dict(schema_version=1, generated_at=stamp(),
                  status='partial' if any(s['status'] != 'ok' for s in sources) else 'collected',
                  sources=sources, counts=dict(collected=len(jobs), candidates=len(visible),
                  excluded=len(jobs)-len(visible), verified_recommendations=0),
                  verification_note='검색후보 및 상세 근거. 필수조건·업무 유사성은 브리핑에서 검증. 경로 미조회.',
                  jobs=visible)
    (output / 'latest.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['source', 'id', 'category', 'reason', 'company', 'title', 'location', 'career', 'deadline',
              'url', 'detail_status', 'detail_checked_at']
    with (output / 'candidates.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(visible)
    status = {k: v for k, v in bundle.items() if k != 'jobs'}
    (output / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Latest collection', '', f"Checked: {bundle['generated_at']}", '',
             'Search candidates only; not verified recommendations.', '']
    for source in sources:
        lines.append(f"- {source['source']}: {source['status']}, {source['collected']} collected")
        for error in source['errors']:
            lines.append(f"  - {error['error']}")
    lines += ['', '| Platform | Company | Job | Region | Career | Deadline | Detail |',
              '|---|---|---|---|---|---|---|']
    clean = lambda s: str(s or '').replace('|', '/').replace('\n', ' ')
    for job in visible:
        lines.append('| ' + ' | '.join([clean(job['source']), clean(job['company']),
            f"[{clean(job['title'])}]({job['url']})", clean(job['location']), clean(job['career']),
            clean(job['deadline']), clean(job['detail_status'])]) + ' |')
    (output / 'latest.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keyword', action='append')
    parser.add_argument('--pages', type=int, choices=range(1, 6), default=2)
    parser.add_argument('--detail-limit', type=int, default=12)
    parser.add_argument('--output', default='daily-results')
    args = parser.parse_args()
    if args.detail_limit < 0:
        parser.error('--detail-limit must be nonnegative')
    client, jobs, sources = PublicClient(), [], []
    for source in ['saramin', 'jobkorea']:
        rows, status = collect_source(client, source, args.keyword or KEYWORDS, args.pages)
        jobs.extend(rows)
        sources.append(status)
    for job in jobs:
        job['category'], job['reason'] = triage(job)
    # Balance detailed checks across the two sources; stop on each site's first error.
    for source in ['saramin', 'jobkorea']:
        selected = [j for j in jobs if j['source'] == source and j['category'] != 'excluded']
        for job in selected[:args.detail_limit]:
            check_detail(client, job)
            if job['detail_status'] == 'error':
                next(s for s in sources if s['source'] == source)['detail_error'] = job['detail_error']
                break
    status = export_bundle(jobs, sources, Path(args.output))
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    # Files are always published, including a truthful failure report.
    return 0 if all(s['status'] == 'ok' for s in sources) else 2


if __name__ == '__main__':
    raise SystemExit(main())
