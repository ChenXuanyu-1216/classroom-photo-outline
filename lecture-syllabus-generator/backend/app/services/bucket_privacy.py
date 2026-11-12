"""Read-only conservative bucket inspection. Never changes resource permissions."""
import json
from app.errors import AppError


def check_bucket_privacy(client, bucket):
    def refuse():
        return AppError('COS_BUCKET_REVIEW_REQUIRED', '存储桶权限尚未确认；请检查桶 ACL、策略及访问角色。', status_code=503)
    try:
        acl = client.get_bucket_acl(Bucket=bucket)
        owner = acl['Owner']['ID']
        grants = acl['AccessControlList']['Grant']
        if not owner or not isinstance(grants, list) or not grants:
            raise refuse()
        for grant in grants:
            grantee = grant['Grantee']
            if grantee.get('URI') or grantee.get('ID') != owner:
                raise refuse()
        try:
            response = client.get_bucket_policy(Bucket=bucket)
        except Exception as error:
            if str(getattr(error, 'get_status_code', lambda: None)()) != '404':
                raise refuse() from None
            response = None
        if response is not None:
            policy = json.loads(response['Policy'])
            statements = policy.get('Statement', policy.get('statement'))
            if not isinstance(statements, list) or any(
                not isinstance(item, dict) or item.get('Effect', item.get('effect')) != 'Deny'
                for item in statements
            ):
                raise refuse()
    except AppError:
        raise
    except Exception:
        raise refuse() from None
    return {'verified': True, 'scope': 'owner-only bucket ACL; no policy Allow statements'}
