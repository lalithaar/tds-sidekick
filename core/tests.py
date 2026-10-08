from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Assignment, AssignmentSlot, Participation, Question, Solution, Validation, ResubmitNotice
from core.views import _review_cap, _reviewer_allowed


def make_user(username, **kw):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password=None,
        is_approved=True, **kw,
    )


class ReviewQueueBase(TestCase):
    """GA with 5 participants, 8 submitted solutions.

    Slots: alice=Q1 (never submits), bob=Q2+Q3, carol=Q1+Q2,
           dave=Q1+Q3, erin=Q2+Q3.  stranger has no slots.
    """

    def setUp(self):
        self.alice = make_user('alice')
        self.bob = make_user('bob')
        self.carol = make_user('carol')
        self.dave = make_user('dave')
        self.erin = make_user('erin')
        self.stranger = make_user('stranger')

        self.ga = Assignment.objects.create(
            title='GA1', status=Assignment.Status.ASSIGNED, num_questions=3)
        self.qs = [Question.objects.create(assignment=self.ga, number=n) for n in (1, 2, 3)]

        plan = {
            self.alice: [1],
            self.bob: [2, 3],
            self.carol: [1, 2],
            self.dave: [1, 3],
            self.erin: [2, 3],
        }
        for user, qnums in plan.items():
            Participation.objects.create(assignment=self.ga, user=user)
            for n in qnums:
                AssignmentSlot.objects.create(assignment=self.ga, user=user, question=self.qs[n - 1])

        self.sols = {}
        for user, qnums in plan.items():
            for n in qnums:
                if user is self.alice:
                    continue
                self.sols[(user, n)] = self.submit(user, n)

    def submit(self, user, qnum, status=Solution.Status.SUBMITTED):
        return Solution.objects.create(
            assignment=self.ga, question=self.qs[qnum - 1], solver=user,
            content_md='my answer', status=status,
            submitted_at=timezone.now() if status != Solution.Status.DRAFT else None,
        )

    def review_url(self, sol):
        return reverse('core:ga_review_solution', args=[self.ga.slug, sol.id])

    def post_review(self, user, sol, working=True, comment='looks fine'):
        return self.client.post(self.review_url(sol), {
            'is_working': 'yes' if working else 'no', 'comment': comment,
        })


class ReviewQueueFilterTests(ReviewQueueBase):
    def test_verified_solution_leaves_queue(self):
        sol = self.sols[(self.carol, 1)]
        Validation.objects.create(solution=sol, reviewer=self.bob, is_working=True, comment='ok')
        Validation.objects.create(solution=sol, reviewer=self.dave, is_working=True, comment='ok')
        sol.status = Solution.Status.VERIFIED
        sol.save(update_fields=['status'])

        self.client.force_login(self.erin)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        self.assertNotIn(sol, list(resp.context['eligible']))

        before = Validation.objects.count()
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertEqual(Validation.objects.count(), before)
        self.assertIn('already has 2 reviews', ' '.join(str(m) for m in resp.context['messages']))

    def test_needs_fix_solution_leaves_queue(self):
        sol = self.sols[(self.erin, 2)]
        Validation.objects.create(solution=sol, reviewer=self.bob, is_working=False, comment='broken')
        sol.status = Solution.Status.NEEDS_FIX
        sol.save(update_fields=['status'])

        self.client.force_login(self.dave)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        self.assertNotIn(sol, list(resp.context['eligible']))

        before = Validation.objects.count()
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertEqual(Validation.objects.count(), before)
        self.assertIn('back to the author', ' '.join(str(m) for m in resp.context['messages']))

    def test_legacy_two_reviews_submitted_blocked(self):
        sol = self.sols[(self.dave, 1)]
        Validation.objects.create(solution=sol, reviewer=self.bob, is_working=True, comment='ok')
        Validation.objects.create(solution=sol, reviewer=self.erin, is_working=False, comment='nope')
        # simulate legacy data stuck in submitted with 2 reviews
        sol.status = Solution.Status.SUBMITTED
        sol.save(update_fields=['status'])

        self.client.force_login(self.carol)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        self.assertNotIn(sol, list(resp.context['eligible']))
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertIn('already has 2 reviews', ' '.join(str(m) for m in resp.context['messages']))

    def test_unreviewed_solutions_appear_in_queue(self):
        from core.views import _nudge_open_count

        self.client.force_login(self.erin)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        eligible = list(resp.context['eligible'])

        self.assertEqual(len(eligible), 6)
        self.assertNotIn(self.sols[(self.erin, 2)], eligible)
        self.assertEqual(len(eligible), _nudge_open_count(self.ga, self.erin))

    def test_stranger_cannot_review(self):
        self.client.force_login(self.stranger)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        self.assertTrue(resp.context['not_opted'])
        self.assertEqual(len(resp.context['eligible']), 0)

        sol = self.sols[(self.carol, 1)]
        before = Validation.objects.count()
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertEqual(Validation.objects.count(), before)
        self.assertIn("haven't opted", ' '.join(str(m) for m in resp.context['messages']))


class ReviewStatusFlowTests(ReviewQueueBase):
    def test_first_rejection_sends_back_to_author(self):
        sol = self.sols[(self.erin, 2)]
        self.client.force_login(self.dave)
        self.post_review(self.dave, sol, working=False, comment='step 3 errors out')
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.NEEDS_FIX)
        self.assertEqual(sol.needs_fix_count, 1)

        # and it is now out of the queue for everyone
        self.client.force_login(self.bob)
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertIn('back to the author', ' '.join(str(m) for m in resp.context['messages']))

    def test_two_approvals_verify(self):
        sol = self.sols[(self.erin, 3)]
        self.client.force_login(self.bob)
        self.post_review(self.bob, sol, working=True)
        self.client.force_login(self.dave)
        self.post_review(self.dave, sol, working=True)
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.VERIFIED)
        self.assertEqual(sol.verifier_count, 2)
        self.assertIsNotNone(sol.verified_at)

    def test_approval_then_rejection_ends_needs_fix(self):
        sol = self.sols[(self.dave, 3)]
        self.client.force_login(self.bob)
        self.post_review(self.bob, sol, working=True)
        self.client.force_login(self.erin)
        self.post_review(self.erin, sol, working=False, comment='output mismatch')
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.NEEDS_FIX)
        self.assertEqual(sol.verifier_count, 1)


class ReviewCapTests(ReviewQueueBase):
    def test_pending_user_capped_at_two(self):
        self.client.force_login(self.alice)
        # alice has not submitted Q1, so Q1 solutions are off-limits to her
        first = self.sols[(self.bob, 2)]
        second = self.sols[(self.bob, 3)]
        blocked = self.sols[(self.carol, 2)]

        self.post_review(self.alice, first)
        self.post_review(self.alice, second)
        self.assertEqual(Validation.objects.filter(reviewer=self.alice).count(), 2)

        before = Validation.objects.count()
        resp = self.client.get(self.review_url(blocked), follow=True)
        self.assertEqual(Validation.objects.count(), before)
        self.assertIn('submit your own solutions first', ' '.join(str(m) for m in resp.context['messages']))

    def test_pending_user_cannot_review_own_question(self):
        self.client.force_login(self.alice)
        sol = self.sols[(self.carol, 1)]  # Q1 is alice's, unsubmitted
        resp = self.client.get(self.review_url(sol), follow=True)
        self.assertIn("Submit your own solution to Q1", ' '.join(str(m) for m in resp.context['messages']))

    def test_submitted_user_gets_default_cap_of_four(self):
        self.assertEqual(_review_cap(self.ga), 4)
        self.client.force_login(self.bob)
        candidates = [
            self.sols[(self.carol, 1)], self.sols[(self.carol, 2)],
            self.sols[(self.dave, 1)], self.sols[(self.dave, 3)],
            self.sols[(self.erin, 2)],
        ]
        for sol in candidates[:4]:
            self.post_review(self.bob, sol)
        self.assertEqual(Validation.objects.filter(reviewer=self.bob).count(), 4)

        before = Validation.objects.count()
        resp = self.client.get(self.review_url(candidates[4]), follow=True)
        self.assertEqual(Validation.objects.count(), before)
        self.assertIn('plenty', ' '.join(str(m) for m in resp.context['messages']))

    def test_cap_bumps_when_reviewers_are_scarce(self):
        ga = Assignment.objects.create(
            title='GA-tiny', status=Assignment.Status.ASSIGNED, num_questions=5)
        solvers = [make_user('tiny1'), make_user('tiny2')]
        for u in solvers:
            Participation.objects.create(assignment=ga, user=u)
        qs = [Question.objects.create(assignment=ga, number=n) for n in range(1, 6)]
        for i, q in enumerate(qs):
            solver = solvers[i % 2]
            AssignmentSlot.objects.create(assignment=ga, user=solver, question=q)
            Solution.objects.create(
                assignment=ga, question=q, solver=solver,
                content_md='x', status=Solution.Status.SUBMITTED, submitted_at=timezone.now(),
            )
        # 5 open solutions -> need 10 reviews across 2 participants -> cap 5
        self.assertEqual(_review_cap(ga), 5)

    def test_reviewer_allowed_values(self):
        self.assertEqual(_reviewer_allowed(self.stranger, self.ga), 0)
        self.assertEqual(_reviewer_allowed(self.alice, self.ga), 2)   # pending
        self.assertEqual(_reviewer_allowed(self.bob, self.ga), 4)     # submitted, default cap
        admin = make_user('boss', role='admin', is_staff=True)
        self.assertIsNone(_reviewer_allowed(admin, self.ga))

    def test_at_limit_hides_review_queue(self):
        self.client.force_login(self.bob)
        for sol in (self.sols[(self.carol, 1)], self.sols[(self.carol, 2)],
                    self.sols[(self.dave, 1)], self.sols[(self.dave, 3)]):
            self.post_review(self.bob, sol)
        resp = self.client.get(reverse('core:ga_review', args=[self.ga.slug]))
        self.assertTrue(resp.context['at_limit'])
        self.assertEqual(len(resp.context['eligible']), 0)
        content = resp.content.decode()
        self.assertIn('review limit', content)
        self.assertNotIn('Nothing to review yet', content)
        self.assertNotIn('>Review</a>', content)


class HomeNudgeTests(ReviewQueueBase):
    def test_nudge_shown_when_solutions_await_review(self):
        self.client.force_login(self.bob)
        resp = self.client.get(reverse('core:home'))
        nudges = resp.context['review_nudges']
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0]['slug'], self.ga.slug)
        self.assertIn('waiting for a second pair of eyes', nudges[0]['text'])

    def test_no_nudge_when_at_cap(self):
        self.client.force_login(self.alice)
        for sol in (self.sols[(self.bob, 2)], self.sols[(self.bob, 3)]):
            self.post_review(self.alice, sol)
        resp = self.client.get(reverse('core:home'))
        self.assertEqual(resp.context['review_nudges'], [])

    def test_no_nudge_for_non_participant(self):
        self.client.force_login(self.stranger)
        resp = self.client.get(reverse('core:home'))
        self.assertEqual(resp.context['review_nudges'], [])


class HomeFollowUpTests(ReviewQueueBase):
    def test_no_all_done_when_fixes_pending(self):
        # carol has submitted everything and done her 2 reviews,
        # so no "steps" remain — but one of her solutions needs fixes
        for sol in (self.sols[(self.bob, 3)], self.sols[(self.dave, 3)]):
            Validation.objects.create(solution=sol, reviewer=self.carol, is_working=True, comment='ok')
        own = self.sols[(self.carol, 1)]
        own.status = Solution.Status.NEEDS_FIX
        own.save(update_fields=['status'])

        self.client.force_login(self.carol)
        resp = self.client.get(reverse('core:home'))
        content = resp.content.decode()
        self.assertNotIn('all done', content)
        self.assertIn('Fix needed', content)
        self.assertNotIn('color:#900', content)

    def test_all_done_shown_when_nothing_pending(self):
        # everyone verifies everything and carol has done her 2 reviews
        for sol in self.sols.values():
            reviewers = [u for u in (self.alice, self.bob, self.carol, self.dave, self.erin)
                         if u != sol.solver]
            for u in reviewers[:2]:
                Validation.objects.get_or_create(
                    solution=sol, reviewer=u, defaults={'is_working': True, 'comment': 'ok'})
            sol.status = Solution.Status.VERIFIED
            sol.verifier_count = 2
            sol.save(update_fields=['status', 'verifier_count'])
        for sol in (self.sols[(self.bob, 3)], self.sols[(self.dave, 3)]):
            Validation.objects.get_or_create(
                solution=sol, reviewer=self.carol,
                defaults={'is_working': True, 'comment': 'ok'})

        self.client.force_login(self.carol)
        resp = self.client.get(reverse('core:home'))
        content = resp.content.decode()
        self.assertIn('all done', content)
        self.assertNotIn('Fix needed', content)


class ResubmitNoticeTests(ReviewQueueBase):
    """A reviewer who flagged a solution gets nudged when the author resubmits."""

    def flag_and_resubmit(self):
        sol = self.sols[(self.carol, 1)]
        self.client.force_login(self.dave)
        self.post_review(self.dave, sol, working=True)
        self.client.force_login(self.bob)
        self.post_review(self.bob, sol, working=False, comment='step 3 fails')
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.NEEDS_FIX)

        slot = AssignmentSlot.objects.get(
            assignment=self.ga, user=self.carol, question=self.qs[0])
        self.client.force_login(self.carol)
        resp = self.client.post(
            reverse('core:ga_mine_submit', args=[self.ga.slug, slot.id]),
            {'content_md': 'fixed: step by step'},
        )
        self.assertEqual(resp.status_code, 302)
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.SUBMITTED)
        return sol

    def test_notice_only_for_reviewers_who_flagged(self):
        sol = self.flag_and_resubmit()
        notices = ResubmitNotice.objects.filter(solution=sol)
        self.assertEqual(notices.count(), 1)
        self.assertEqual(notices.get().reviewer, self.bob)

        self.client.force_login(self.bob)
        content = self.client.get(reverse('core:home')).content.decode()
        self.assertIn('Take another look', content)
        self.assertIn(self.review_url(sol), content)

        self.client.force_login(self.dave)
        content = self.client.get(reverse('core:home')).content.decode()
        self.assertNotIn('another look', content)

    def test_notice_clears_after_re_review(self):
        sol = self.flag_and_resubmit()
        self.client.force_login(self.bob)
        self.post_review(self.bob, sol, working=True)
        content = self.client.get(reverse('core:home')).content.decode()
        self.assertNotIn('another look', content)

    def test_notice_hidden_once_verified(self):
        sol = self.flag_and_resubmit()
        for reviewer in (self.dave, self.erin):
            self.client.force_login(reviewer)
            self.post_review(reviewer, sol, working=True)
        sol.refresh_from_db()
        self.assertEqual(sol.status, Solution.Status.VERIFIED)
        self.client.force_login(self.bob)
        content = self.client.get(reverse('core:home')).content.decode()
        self.assertNotIn('another look', content)
