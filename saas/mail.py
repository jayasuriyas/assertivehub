import os

from django.core.mail import EmailMessage


def send(to, subject, body):
    host = os.environ.get('HUB_EMAIL_HOST')
    try:
        if host:
            from django.core.mail import get_connection
            conn = get_connection('django.core.mail.backends.smtp.EmailBackend', host=host, port=int(os.environ.get('HUB_EMAIL_PORT', '587')),
                                  username=os.environ.get('HUB_EMAIL_USER', ''), password=os.environ.get('HUB_EMAIL_PASS', ''),
                                  use_tls=os.environ.get('HUB_EMAIL_TLS', '1') == '1', timeout=10)
            EmailMessage(subject, body, os.environ.get('HUB_EMAIL_FROM', os.environ.get('HUB_EMAIL_USER', '')), [to], connection=conn).send()
        else:
            print('[mail to %s] %s' % (to, subject))
        return True
    except Exception as e:
        print('mail failed:', e)
        return False
