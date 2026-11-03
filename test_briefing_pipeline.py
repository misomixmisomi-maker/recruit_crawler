import unittest
from briefing_pipeline import parse_jobkorea, parse_saramin, triage, record


class PipelineTests(unittest.TestCase):
    def test_legacy_jobkorea_card(self):
        html = '''<ul><li><a class="name">Company A</a>
        <a href="/Recruit/GI_Read/12345?tracking=1">AI 콘텐츠 디자이너 신입 모집</a>
        서울 마포구 신입·경력 ~ 11/30</li></ul>'''
        jobs = parse_jobkorea(html)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['id'], '12345')
        self.assertEqual(jobs[0]['location'], '서울 마포구')
        self.assertEqual(jobs[0]['career'], '신입·경력')
        self.assertNotIn('tracking', jobs[0]['url'])

    def test_metadata_missing_is_not_entry_level(self):
        jobs = parse_jobkorea('<a href="/Recruit/GI_Read/17">콘텐츠 디자이너 채용</a>')
        self.assertEqual(jobs[0]['career'], '')
        self.assertEqual(triage(jobs[0])[0], 'conditional')

    def test_jsonld_and_tracking_dedup(self):
        html = '''<script type="application/ld+json">{"@graph":[{"@type":"JobPosting",
        "title":"콘텐츠 디자이너", "url":"https://www.jobkorea.co.kr/Recruit/GI_Read/88",
        "hiringOrganization":{"name":"A"},"validThrough":"2026-11-30"}]}</script>
        <a href="/Recruit/GI_Read/88?a=1">콘텐츠 디자이너</a>'''
        self.assertEqual(len(parse_jobkorea(html)), 1)

    def test_filters(self):
        base = record('saramin', '1', 'https://example.com', '콘텐츠 디자이너', location='서울 마포구')
        for career, expected in [('신입·경력', 'conditional'), ('경력무관', 'conditional'),
                                 ('경력 1년↑', 'excluded'), ('경력 없음', 'conditional')]:
            self.assertEqual(triage(dict(base, career=career))[0], expected)
        self.assertEqual(triage(dict(base, location='경기 하남시'))[0], 'excluded')
        self.assertEqual(triage(dict(base, title='백엔드 개발자'))[0], 'excluded')

    def test_saramin_card(self):
        html = '''<div class="item_recruit"><div class="area_job"><h2 class="job_tit">
        <a href="/zf_user/jobs/view?rec_idx=123&amp;foo=1">콘텐츠 디자이너</a></h2>
        <div class="job_date"><span class="date">~ 11/30</span></div>
        <div class="job_condition"><span><a>서울</a><a>마포구</a></span><span>신입</span>
        <span>학력무관</span><span>정규직</span></div></div>
        <div class="area_corp"><strong class="corp_name"><a>A</a></strong></div></div>'''
        job = parse_saramin(html)[0]
        self.assertEqual(job['id'], '123')
        self.assertEqual(job['career'], '신입')
        self.assertNotIn('foo', job['url'])


if __name__ == '__main__':
    unittest.main()
