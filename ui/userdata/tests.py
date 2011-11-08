"""
This file demonstrates two different styles of tests (one doctest and one
unittest). These will both pass when you run "manage.py test".

Replace these with more appropriate tests for your application.
"""

from django.test import TestCase
from django.conf import settings
from django.test.client import Client
from django.core.urlresolvers import clear_url_caches
from django.utils import simplejson as json
from django.conf import settings

from synnefo.ui.userdata.models import User
from synnefo.ui.userdata.models import *

class AaiClient(Client):

    def request(self, **request):
        request['HTTP_X_AUTH_TOKEN'] = '46e427d657b20defe352804f0eb6f8a2'
        return super(AaiClient, self).request(**request)

class TestRestViews(TestCase):

    fixtures = ['users']

    def setUp(self):
        settings.ROOT_URLCONF = 'synnefo.ui.userdata.urls'
        settings.SKIP_SSH_VALIDATION = True
        clear_url_caches()
        self.client = AaiClient()
        self.user = User.objects.get(pk=1)

    def test_keys_collection_get(self):
        resp = self.client.get("/keys")
        self.assertEqual(resp.content, "[]")

        PublicKeyPair.objects.create(user=self.user, name="key pair 1",
                content="content1")

        resp = self.client.get("/keys")
        self.assertEqual(resp.content, """[{"content": "content1", "id": 1, "uri": "/keys/1", "name": "key pair 1", "fingerprint": ""}]""")

        PublicKeyPair.objects.create(user=self.user, name="key pair 2",
                content="content2")

        resp = self.client.get("/keys")
        self.assertEqual(resp.content, """[{"content": "content1", "id": 1, "uri": "/keys/1", "name": "key pair 1", "fingerprint": ""}, {"content": "content2", "id": 2, "uri": "/keys/2", "name": "key pair 2", "fingerprint": ""}]""")

    def test_keys_resourse_get(self):
        resp = self.client.get("/keys/1")
        self.assertEqual(resp.status_code, 404)

        # create a public key
        PublicKeyPair.objects.create(user=self.user, name="key pair 1",
                content="content1")
        resp = self.client.get("/keys/1")
        self.assertEqual(resp.content, """{"content": "content1", "id": 1, "uri": "/keys/1", "name": "key pair 1", "fingerprint": ""}""")

        # update
        resp = self.client.put("/keys/1", json.dumps({'name':'key pair 1 new name'}),
                content_type='application/json')
        pk = PublicKeyPair.objects.get(pk=1)
        self.assertEqual(pk.name, "key pair 1 new name")

        # delete
        resp = self.client.delete("/keys/1")
        self.assertEqual(PublicKeyPair.objects.count(), 0)

        resp = self.client.get("/keys/1")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.get("/keys")
        self.assertEqual(resp.content, "[]")

        # test rest create
        resp = self.client.post("/keys", json.dumps({'name':'key pair 2',
            'content':"""key 2 content"""}),
                content_type='application/json')
        self.assertEqual(PublicKeyPair.objects.count(), 1)
        pk = PublicKeyPair.objects.get(pk=1)
        self.assertEqual(pk.name, "key pair 2")
        self.assertEqual(pk.content, "key 2 content")

    def test_generate_views(self):
        import base64

        # just test that
        resp = self.client.get("/keys/generate")
        self.assertNotEqual(resp, "")

        data = json.loads(resp.content)
        self.assertEqual(data.has_key('private'), True)
        self.assertEqual(data.has_key('private'), True)

        # public key is base64 encoded
        base64.b64decode(data['public'].replace("ssh-rsa ",""))

        # remove header/footer
        private = "".join(data['private'].split("\n")[1:-1])

        # private key is base64 encoded
        base64.b64decode(private)

    def test_invalid_data(self):
        resp = self.client.post("/keys", json.dumps({'content':"""key 2 content"""}),
                content_type='application/json')

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.content, """{"non_field_key": "__all__", "errors": """
                                       """{"name": ["This field cannot be blank."]}}""")

        settings.USERDATA_MAX_SSH_KEYS_PER_USER = 2

        # test ssh limit
        resp = self.client.post("/keys", json.dumps({'name':'key1', 'content':"""key 1 content"""}),
                content_type='application/json')
        resp = self.client.post("/keys", json.dumps({'name':'key1', 'content':"""key 1 content"""}),
                content_type='application/json')
        resp = self.client.post("/keys", json.dumps({'name':'key1', 'content':"""key 1 content"""}),
                content_type='application/json')
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.content, """{"non_field_key": "__all__", "errors": """
                                       """{"__all__": ["SSH keys limit exceeded."]}}""")
