import pytest
from app.errors import AppError
from storage_fakes import MissingObject


@pytest.mark.parametrize('policy', [None, {'Statement': [{'Effect': 'Allow', 'Principal': '*'}]}])
def test_bucket_check_does_not_treat_private_object_acl_as_private_bucket(policy):
    from app.services.bucket_privacy import check_bucket_privacy
    class Client:
        def get_bucket_acl(self, **kwargs):
            return {'Owner': {'ID': 'owner'}, 'AccessControlList': {'Grant': [{'Grantee': {'ID': 'owner'}, 'Permission': 'FULL_CONTROL'}]}}
        def get_bucket_policy(self, **kwargs):
            if policy is None:
                raise MissingObject()
            import json
            return {'Policy': json.dumps(policy)}
    if policy is None:
        assert check_bucket_privacy(Client(), 'fixture')['verified'] is True
    else:
        with pytest.raises(AppError):
            check_bucket_privacy(Client(), 'fixture')


def test_bucket_check_refuses_group_or_unknown_acl():
    from app.services.bucket_privacy import check_bucket_privacy
    class Client:
        def get_bucket_acl(self, **kwargs):
            return {'Owner': {'ID': 'owner'}, 'AccessControlList': {'Grant': [{'Grantee': {'URI': 'AllUsers'}, 'Permission': 'READ'}]}}
    with pytest.raises(AppError):
        check_bucket_privacy(Client(), 'fixture')
