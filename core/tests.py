from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Assignment, AssignmentSlot, Participation, Question, Solution, Validation
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
