"""Network-only COS fixture; application storage, DB and file routes remain real."""
from io import BytesIO


class MissingObject(Exception):
    def get_status_code(self):
        return 404


class Body:
    def __init__(self, content):
        self.content = content

    def get_raw_stream(self):
        return BytesIO(self.content)


class FakeCosClient:
    def __init__(self):
        self.objects = {}
        self.access = {}

    def put_object(self, Bucket, Key, Body, ACL=None):
        self.objects[(Bucket, Key)] = Body.read() if hasattr(Body, 'read') else bytes(Body)
        self.access[(Bucket, Key)] = ACL
        return {'ETag': 'fixture'}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise MissingObject()
        return {'Content-Length': str(len(self.objects[(Bucket, Key)]))}

    def get_object(self, Bucket, Key, Range=None):
        if (Bucket, Key) not in self.objects:
            raise MissingObject()
        content = self.objects[(Bucket, Key)]
        if Range is not None:
            start, end = map(int, Range.removeprefix('bytes=').split('-'))
            content = content[start:end + 1]
        return {'Body': Body(content)}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        self.access.pop((Bucket, Key), None)
        return {}
