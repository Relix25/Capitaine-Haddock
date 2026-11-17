"""Turn identity, fairness and stale-publication regressions, without transports."""
from dataclasses import replace
from datetime import datetime, timezone
import unittest

from courswatch.conversation.contracts import MediaDescriptor, MessageEnvelope, Scope
from courswatch.conversation.coordinator import Coordinator, QueueFull
from courswatch.conversation.participation import decide


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.0]
        self.coordinator=Coordinator(clock=lambda:self.now[0])
        self.scope=Scope('guild','channel')

    def message(self,identity='1',author='alice',text='Haddock, date du projet AAIT ?',**changes):
        values=dict(scope=self.scope,message_id=identity,author_id=author,display_name='Même nom',
            text=text,created_at=datetime.now(timezone.utc).isoformat())
        values.update(changes)
        return MessageEnvelope(**values)

    def add(self,message,direct=True):
        self.coordinator.observe(message)
        return self.coordinator.enqueue(message,direct)

    def test_two_authors_and_two_topics_keep_distinct_targets_and_fifo(self):
        alice=self.add(self.message())
        bob=self.add(self.message('2','bob','Haddock, peux-tu expliquer le projet BDFA ?'))
        self.assertIsNot(alice,bob)
        self.assertEqual((alice.requester_ids,bob.requester_ids),(['alice'],['bob']))
        self.assertEqual(self.coordinator.next().reply_target_id,'1')
        self.coordinator.finish(alice)
        self.assertEqual(self.coordinator.next().reply_target_id,'2')

    def test_same_author_two_projects_are_not_coalesced(self):
        first=self.add(self.message())
        second=self.add(self.message('2',text='Haddock, une précision sur BDFA.'))
        self.assertIsNot(first,second)

    def test_explicit_fragment_keeps_all_triggers_and_last_same_author_target(self):
        first=self.add(self.message(text='Haddock, je voulais savoir…'))
        fragment=self.message('2',text='La date du projet AAIT ?',reply_to_message_id='1')
        second=self.add(fragment,False)
        self.assertIs(first,second)
        snapshot=self.coordinator.freeze(first)
        self.assertEqual(snapshot.trigger_message_ids,('1','2'))
        self.assertEqual(snapshot.reply_target_id,'2')
        self.assertEqual(len(snapshot.as_context()),2)

    def test_third_author_correction_keeps_requester_and_reply_target(self):
        turn=self.add(self.message())
        old=self.coordinator.freeze(turn)
        addition=self.message('2','bob','Précision : le sujet est reporté.',reply_to_message_id='1')
        self.add(addition,False)
        self.assertEqual(turn.requester_ids,['alice'])
        self.assertEqual(turn.reply_target_id,'1')
        self.assertFalse(self.coordinator.valid(old))
        self.assertTrue(self.coordinator.retry(turn))
        revised = self.coordinator.freeze(turn)
        self.assertEqual(len(revised.messages), 2)
        self.assertEqual(revised.trigger_message_ids, ('1',))
        self.assertIn('le sujet est reporté', revised.goal)
        self.coordinator.observe(replace(addition, text='Précision : le projet BDFA est reporté.', revision=2))
        self.assertEqual(self.coordinator.freeze(turn).goal, 'Haddock, date du projet AAIT ?')
        self.assertFalse(self.coordinator.valid(revised))

    def test_two_authors_same_exact_question_can_share_a_target(self):
        first=self.add(self.message())
        shared=self.add(self.message('2','bob'))
        self.assertIs(first,shared)
        self.assertEqual(shared.requester_ids,['alice','bob'])
        self.assertEqual(shared.reply_target_id,'1')

    def test_identical_media_questions_keep_distinct_occurrences(self):
        media = (MediaDescriptor('asset', 'image', 'https://cdn.discordapp.com/attachments/1/2/a.png'),)
        first = self.add(self.message(text='Haddock, décris cette image.', media=media))
        second = self.add(self.message('2', 'bob', 'Haddock, décris cette image.', media=media))
        self.assertIsNot(first, second)

    def test_linked_independent_question_keeps_own_requester_and_target(self):
        original = self.add(self.message())
        original.state = 'generating'
        snapshot = self.coordinator.freeze(original)
        followup = self.add(self.message('2', 'bob', 'Haddock, où se trouve la salle BDFA ?', reply_to_message_id='1'))
        self.assertIsNot(original, followup)
        self.assertEqual((original.requester_ids, followup.requester_ids), (['alice'], ['bob']))
        self.assertEqual((original.reply_target_id, followup.reply_target_id), ('1', '2'))
        self.assertTrue(self.coordinator.valid(snapshot))

    def test_withdrawal_does_not_withdraw_shared_request_for_other_author(self):
        turn = self.add(self.message())
        self.add(self.message('2', 'bob'))
        snapshot = self.coordinator.freeze(turn)
        self.add(self.message('3', 'alice', 'Laisse tomber.', reply_to_message_id='1'), False)
        self.assertFalse(self.coordinator.valid(snapshot))
        self.assertTrue(self.coordinator.retry(turn))
        current = self.coordinator.freeze(turn)
        self.assertEqual(current.requester_ids, ('bob',))
        self.assertEqual(current.reply_target_id, '2')
        self.assertEqual(current.trigger_message_ids, ('2',))

    def test_unrelated_message_does_not_invalidate_snapshot(self):
        turn=self.add(self.message())
        snapshot=self.coordinator.freeze(turn)
        self.add(self.message('2','bob','Haddock, où est le projet BDFA ?'))
        self.assertTrue(self.coordinator.valid(snapshot))
        self.assertNotIn('BDFA',str(snapshot.as_context()))

    def test_edit_invalidates_and_retries_once_with_revised_goal(self):
        message=self.message()
        turn=self.add(message)
        turn.state='generating';turn.attempts=1
        snapshot=self.coordinator.freeze(turn)
        self.coordinator.observe(replace(message,text='Haddock, date de présentation AAIT ?',revision=2,edited_at='2026-11-17T12:00:00+00:00'))
        self.assertFalse(self.coordinator.valid(snapshot))
        self.assertTrue(self.coordinator.retry(turn))
        self.assertIn('présentation',self.coordinator.freeze(turn).goal)
        turn.attempts=2
        self.assertFalse(self.coordinator.retry(turn))

    def test_human_answer_or_cancellation_prevents_publication_and_retry(self):
        for text,author in [('La date est le 18 novembre.','bob'),('Laisse tomber.','alice')]:
            with self.subTest(text=text):
                self.setUp()
                turn=self.add(self.message())
                snapshot=self.coordinator.freeze(turn)
                self.add(self.message('2',author,text,reply_to_message_id='1'),False)
                self.assertFalse(self.coordinator.valid(snapshot))
                self.assertFalse(self.coordinator.retry(turn))

    def test_deletion_is_not_retried_and_old_create_cannot_resurrect(self):
        message=self.message()
        turn=self.add(message)
        snapshot=self.coordinator.freeze(turn)
        self.coordinator.delete(self.scope,'1')
        self.assertFalse(self.coordinator.valid(snapshot))
        self.assertFalse(self.coordinator.observe(message))
        self.assertFalse(self.coordinator.retry(turn))

    def test_direct_priority_and_bounded_queue_explicit_rejection(self):
        self.coordinator.max_pending=2
        ambient=self.add(self.message(text='Le cours commence demain matin.'),False)
        direct=self.add(self.message('2','bob','Haddock, explique BDFA.'))
        self.assertIs(self.coordinator.next(),direct)
        with self.assertRaises(QueueFull):
            self.add(self.message('3','charlie','Haddock, où est SOS ?'))
        self.assertEqual(ambient.state,'queued')
        self.assertEqual(direct.state,'queued')

    def test_restart_new_message_has_new_turn_identity(self):
        first=self.add(self.message())
        restarted=Coordinator()
        second=self.message('2')
        restarted.observe(second)
        second_turn=restarted.enqueue(second,True)
        self.assertNotEqual(first.turn_id,second_turn.turn_id)

    def test_same_message_id_in_other_scope_remains_distinct(self):
        first=self.add(self.message())
        second=self.add(self.message(scope=Scope('guild','other')))
        self.assertNotEqual(first.turn_id,second.turn_id)
        self.assertEqual(len(self.coordinator.turns),2)

    def test_source_attribution_is_stable_for_homonyms(self):
        first=self.add(self.message())
        self.add(self.message('2','bob','Précision : report annoncé.',reply_to_message_id='1'),False)
        context=self.coordinator.freeze(first).as_context()
        self.assertEqual([row['speaker'] for row in context],['Même nom','Même nom'])
        self.assertEqual([row['author_id'] for row in context],['alice','bob'])
        self.assertEqual(context[1]['reply_to_message_id'],'1')

    def test_policies_keep_backfill_acknowledgements_and_human_chat_silent(self):
        self.assertEqual(decide(self.message(ingestion_mode='backfill'),direct=True).decision,'silence')
        self.assertEqual(decide(self.message(text='Merci Haddock !'),direct=True).decision,'silence')
        self.assertEqual(decide(self.message(text="J'aime Tintin"),direct=False).decision,'wait')
        self.assertEqual(decide(self.message(mentioned_user_ids=('human',)),direct=False).decision,'silence')
        incomplete=decide(self.message(text='Haddock, je voulais savoir…'),direct=True)
        self.assertEqual(incomplete.decision,'wait')
        self.assertEqual(incomplete.recheck_after_ms,600)

    def test_memory_is_bounded_without_dropping_pending_dependencies(self):
        turn=self.add(self.message())
        for i in range(600):
            self.coordinator.observe(self.message(str(i+2),text='autre sujet'))
        self.coordinator.prune()
        self.assertLessEqual(len(self.coordinator.messages),513)
        self.assertTrue(self.coordinator.active(self.scope,'1'))
        self.assertTrue(self.coordinator.valid(self.coordinator.freeze(turn)))


    def test_linked_ignorance_questions_or_emoji_do_not_resolve_request(self):
        for text in ('Je ne sais pas.', 'Quelqu’un sait ?', '🤷', 'Bonne question !', 'Merci !',
                     'Laisse tomber.', 'J’ai cours lundi.', 'La date BDFA est le 20 novembre.'):
            with self.subTest(text=text):
                self.setUp()
                turn=self.add(self.message())
                turn.state = 'generating'
                snapshot = self.coordinator.freeze(turn)
                self.add(self.message('2','bob',text,reply_to_message_id='1'),False)
                self.assertTrue(self.coordinator.valid(snapshot))
                self.assertEqual(turn.requester_ids, ['alice'])

    def test_silent_continuation_can_join_but_cannot_open_a_turn(self):
        aside = self.message('2', text='Précision : le projet est celui de Crafter.')
        self.coordinator.observe(aside)
        self.assertIsNone(self.coordinator.enqueue(aside, False, allow_new=False))
        turn = self.add(self.message(text='Haddock, je voulais savoir…'))
        self.assertIs(self.coordinator.enqueue(aside, False, allow_new=False), turn)
        self.assertIn('Crafter', self.coordinator.freeze(turn).goal)

    def test_late_source_expires_during_generation(self):
        request = self.message()
        self.coordinator.observe(request)
        turn = self.coordinator.enqueue(request, True, gateway_ms=119_000, received_at=99.75)
        self.assertEqual(turn.expires_at, 100.75)
        self.assertEqual(turn.received_at, 99.75)
        turn.state = 'generating'
        snapshot = self.coordinator.freeze(turn)
        self.now[0] = 101
        self.assertFalse(self.coordinator.valid(snapshot))
        self.assertFalse(self.coordinator.retry(turn))

if __name__=='__main__':
    unittest.main()
