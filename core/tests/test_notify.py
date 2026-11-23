"""Notification service: in-app at once, consent and verification gates, dedupe, quiet hours, provider sending
(mocked), SMS fallback, webhooks with signatures, STOP keyword, campaigns, secrets never exposed."""
import datetime
import hashlib
import hmac
import json
from unittest import mock

from core.models import ContactProfile, NotificationDelivery as D, NotificationJob, NotificationProvider as P, NotificationTemplate as T, Record
from core.v6 import crypto, notify as N

from .helpers import Base


def setup_providers():
    wa = P.objects.create(channel='whatsapp', provider='meta_cloud', enabled=True, status='verified', webhook_token='watok',
                          config_public={'phone_number_id': '123', 'verify_token': 'vt'}, secrets_enc=crypto.enc({'token': 'SECRET-TOKEN-123', 'app_secret': 'appsec'}))
    sms = P.objects.create(channel='sms', provider='msg91', enabled=True, status='verified', webhook_token='smstok', config_public={'sender': 'ACMEHR'},
                           secrets_enc=crypto.enc({'authkey': 'AUTHKEY-999'}))
    N.ensure_defaults()
    T.objects.filter(channel='whatsapp').update(approval_status='approved')
    T.objects.filter(channel='sms').update(provider_template_id='1207160000000000001')
    return wa, sms


def verified(eid, wa=True, sms=True, quiet=None):
    c = N.contact(eid)
    c.phone_e164, c.phone_verified_at, c.wa_opt_in, c.sms_opt_in = '+919847012345', datetime.datetime.now(datetime.timezone.utc), wa, sms
    c.prefs = {'quiet': quiet or {'from': '00:00', 'to': '00:00'}}
    c.save()
    return c


class Notify(Base):
    def test_phone_normalisation(self):
        for raw in ('98470 12345', '+91-9847012345', '09847012345', '919847012345'):
            self.assertEqual(N.normalize_phone(raw), '+919847012345', raw)
        self.assertEqual(N.normalize_phone('12345'), '')
        self.assertEqual(N.normalize_phone('5847012345'), '')

    def test_inapp_created_immediately_and_deduped(self):
        N.emit('task.assigned', 'task', 't_rahul', 'to:e_rahul', ['e_rahul'], {'task_id': 't_rahul'})
        N.emit('task.assigned', 'task', 't_rahul', 'to:e_rahul', ['e_rahul'], {'task_id': 't_rahul'})
        d = D.objects.filter(channel='inapp')
        self.assertEqual(d.count(), 1)
        self.assertIn('Task t_rahul', d[0].title)
        inbox = self.api(self.em, 'notify.inbox')
        self.assertEqual(inbox['unread'], 1)

    def test_task_save_triggers_assignment(self):
        t = {'id': 'tn', 'title': 'Landing page', 'desc': '', 'eid': 'e_rahul', 'cid': '', 'pri': 'med', 'status': 'todo', 'due': '2026-12-04', 'cat': 'Website', 'qty': 1, 'reopens': [], 'mistakes': [], 'est': 300}
        self.save(self.bm, [{'c': 'tasks', 'op': 'set', 'rec': t}])
        self.save(self.bm, [{'c': 'tasks', 'op': 'set', 'rec': dict(t, title='Landing page v2')}])
        self.assertEqual(NotificationJob.objects.filter(event_key='task.assigned').count(), 1)
        rec = Record.objects.get(coll='tasks', rid='tn').data
        self.assertEqual(rec['createdBy'], str(self.bm.pk))

    def test_no_whatsapp_without_verification_and_consent(self):
        setup_providers()
        N.emit('task.assigned', 'task', 't_rahul', 'x', ['e_rahul'], {'task_id': 't_rahul'})
        sk = D.objects.filter(channel__in=['whatsapp', 'sms'])
        self.assertTrue(sk.exists() and all(x.status == 'skipped' for x in sk))
        self.assertEqual({x.skip_reason for x in sk}, {'not_verified'})

    def test_whatsapp_send_and_sms_fallback_on_permanent_failure(self):
        setup_providers()
        verified('e_rahul')
        N.emit('leave.decided', 'leave', 'lv1', 'Approved', ['e_rahul'], {'emp_id': 'e_rahul'})
        wa = D.objects.get(channel='whatsapp')
        self.assertEqual(wa.status, 'queued')
        with mock.patch.object(N.MetaCloud, 'send', side_effect=N.SendError('Not a WhatsApp user', temporary=False, code='131026')), \
             mock.patch.object(N.Msg91, 'send', return_value='sms-1') as ms:
            N.send_due()
            N.send_due()
        wa.refresh_from_db()
        self.assertEqual(wa.status, 'failed_perm')
        sms = D.objects.get(channel='sms')
        self.assertEqual((sms.status, sms.fallback_of_id, sms.provider_msg_id), ('sent', wa.pk, 'sms-1'))
        self.assertEqual(ms.call_count, 1)

    def test_temporary_failure_retries(self):
        setup_providers()
        verified('e_rahul')
        N.emit('leave.decided', 'leave', 'lv2', 'Approved', ['e_rahul'], {'emp_id': 'e_rahul'})
        with mock.patch.object(N.MetaCloud, 'send', side_effect=N.SendError('timeout', temporary=True)):
            N.send_due()
        wa = D.objects.get(channel='whatsapp')
        self.assertEqual((wa.status, wa.attempts), ('queued', 1))
        self.assertGreater(wa.next_attempt_at, N.now())

    def test_preference_off_skips_but_mandatory_does_not(self):
        setup_providers()
        c = verified('e_rahul')
        c.prefs = {'groups': {'tasks': False, 'leave': False}, 'quiet': {'from': '00:00', 'to': '00:00'}}
        c.save()
        N.emit('task.assigned', 'task', 't_rahul', 'x', ['e_rahul'], {'task_id': 't_rahul'})
        self.assertEqual(D.objects.get(job__event_key='task.assigned', channel='whatsapp').skip_reason, 'pref_off')
        N.emit('leave.decided', 'leave', 'lv3', 'Approved', ['e_rahul'], {'emp_id': 'e_rahul'})
        self.assertEqual(D.objects.get(job__event_key='leave.decided', channel='whatsapp').status, 'queued')

    def test_quiet_hours_delay(self):
        setup_providers()
        verified('e_rahul', quiet={'from': '00:00', 'to': '23:59'})
        N.emit('task.assigned', 'task', 't_rahul', 'x', ['e_rahul'], {'task_id': 't_rahul'})
        d = D.objects.get(channel='whatsapp')
        self.assertEqual(d.skip_reason, 'quiet_delayed')
        self.assertGreater(d.next_attempt_at, N.now())

    def test_cancelled_meeting_reminder_not_sent(self):
        N.emit('meeting.reminder', 'meeting', '999', '15:x', ['e_rahul'], {'meeting_id': 999, 'start': 'x'})
        self.assertEqual(NotificationJob.objects.get(event_key='meeting.reminder').status, 'cancelled')

    def test_webhook_signature_status_and_stop(self):
        wa, _ = setup_providers()
        verified('e_rahul')
        N.emit('leave.decided', 'leave', 'lv4', 'Approved', ['e_rahul'], {'emp_id': 'e_rahul'})
        with mock.patch.object(N.MetaCloud, 'send', return_value='wamid.1'):
            N.send_due()
        body = json.dumps({'entry': [{'changes': [{'value': {'statuses': [{'id': 'wamid.1', 'status': 'read', 'timestamp': '1764000000'}],
                                                             'messages': [{'from': '919847012345', 'text': {'body': 'STOP'}}]}}]}]}).encode()
        bad = self.client.post('/hooks/whatsapp/watok', data=body, content_type='application/json', HTTP_X_HUB_SIGNATURE_256='sha256=bad')
        self.assertEqual(bad.status_code, 403)
        sig = 'sha256=' + hmac.new(b'appsec', body, hashlib.sha256).hexdigest()
        ok = self.client.post('/hooks/whatsapp/watok', data=body, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=sig)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(D.objects.get(channel='whatsapp').status, 'read')
        self.assertFalse(ContactProfile.objects.get(emp_id='e_rahul').wa_opt_in)
        v = self.client.get('/hooks/whatsapp/watok', {'hub.mode': 'subscribe', 'hub.verify_token': 'vt', 'hub.challenge': '42'})
        self.assertEqual(v.content, b'42')

    def test_provider_secrets_never_returned(self):
        r = self.api(self.sa, 'notify.providers.save', {'channel': 'whatsapp', 'provider': 'meta_cloud', 'config': {'phone_number_id': '123'},
                                                      'secrets': {'token': 'TOPSECRET-abcd', 'app_secret': 'APPSECRET-wxyz'}}, method='POST')
        self.assertTrue(r['ok'], r)
        blob = json.dumps(r, ensure_ascii=False) + json.dumps(self.api(self.sa, 'notify.providers.list'), ensure_ascii=False) + json.dumps(self.load(self.sa), ensure_ascii=False)
        self.assertNotIn('TOPSECRET', blob)
        self.assertIn('••••abcd', blob)
        self.assertEqual(self.api(self.ca, 'notify.providers.list')['_status'], 403)

    def test_campaign_preview_scope_and_send(self):
        setup_providers()
        verified('e_rahul')
        tpl = T.objects.get(event_key='branch.announcement', channel='whatsapp', active=True)
        pv = self.api(self.hr, 'notify.audience.preview', {'audience': {'branches': [str(self.blr.pk)]}, 'channel': 'whatsapp', 'templateId': str(tpl.pk)}, method='POST')
        self.assertEqual(pv['total'], 4)
        self.assertEqual(pv['willReceive']['whatsapp'], 1)
        pv2 = self.api(self.hr, 'notify.audience.preview', {'audience': {'branches': [str(self.maa.pk)]}, 'channel': 'whatsapp', 'templateId': str(tpl.pk)}, method='POST')
        self.assertEqual(pv2['total'], 0)  # HR is not authorised for Chennai
        r = self.api(self.hr, 'notify.campaigns.create', {'audience': {'branches': [str(self.blr.pk)]}, 'channel': 'whatsapp', 'templateId': str(tpl.pk),
                                                          'variables': {'announcement_title': 'Office closed Friday'}}, method='POST')
        self.assertTrue(r['ok'], r)
        self.assertEqual(D.objects.filter(job__campaign_id=r['campaign']['id'], channel='whatsapp', status='queued').count(), 1)
        self.assertEqual(D.objects.filter(job__campaign_id=r['campaign']['id'], channel='inapp').count(), 4)

    def test_otp_verify_and_optin(self):
        setup_providers()
        with mock.patch.object(N.Msg91, 'send', return_value='otp') as ms, mock.patch('core.v6.notify.secrets.randbelow', return_value=123456):
            r = self.api(self.em, 'notify.contact.sendOtp', {'phone': '98470 12345'}, method='POST')
            self.assertTrue(r['ok'], r)
        self.assertEqual(self.api(self.em, 'notify.contact.verify', {'code': '000000'}, method='POST')['code'], 'VALIDATION')
        r = self.api(self.em, 'notify.contact.verify', {'code': '123456'}, method='POST')
        self.assertTrue(r['contact']['verified'])
        r = self.api(self.em, 'notify.contact.optin', {'channel': 'whatsapp', 'on': True}, method='POST')
        self.assertTrue(r['contact']['whatsapp'])

    def test_phone_change_resets_verification(self):
        verified('e_rahul')
        e = dict(Record.objects.get(coll='employees', rid='e_rahul').data, phone='9000000001')
        self.save(self.ba, [{'c': 'employees', 'op': 'set', 'rec': e}])
        c = ContactProfile.objects.get(emp_id='e_rahul')
        self.assertIsNone(c.phone_verified_at)
        self.assertFalse(c.wa_opt_in)

    def test_leave_flow_notifies_manager_then_employee(self):
        lv = {'id': 'lvx', 'eid': 'e_rahul', 'kind': 'full', 'from': '2026-12-10', 'to': '2026-12-11', 'status': 'Pending', 'reason': 'Family'}
        self.save(self.em, [{'c': 'leaves', 'op': 'set', 'rec': lv}])
        j = NotificationJob.objects.get(event_key='leave.submitted')
        self.assertIn('e_arjun', j.recipients)
        self.assertIn('e_kiran', j.recipients)  # branch admin of Bangalore
        self.save(self.ca, [{'c': 'leaves', 'op': 'set', 'rec': dict(lv, status='Approved')}])
        d = D.objects.get(job__event_key='leave.decided', channel='inapp')
        self.assertIn('approved', d.rendered)
        self.assertIn('Thu, 10 Dec', d.rendered)
