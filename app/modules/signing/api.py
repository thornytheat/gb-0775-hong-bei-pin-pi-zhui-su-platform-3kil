"""整克卷联签发接口。"""
from flask import Blueprint, jsonify, request

from app.api import cabinet
from app.errors import RoastError
from app.modules.signing.service import issue, overview

signing = Blueprint('roast_signing', __name__, url_prefix='/api/signing')


@signing.get('/runs/<key>')
def run_overview(key):
    return jsonify(overview(cabinet(), key))


@signing.post('/runs/<key>/issue')
def issue_packages(key):
    body = request.get_json(silent=True) or {}
    requested = body.get('packages')
    if not isinstance(requested, list) or not requested \
            or not all(isinstance(code, str) for code in requested):
        raise RoastError('BAD_REQUEST', '请给出要签发的包装号列表 packages', 400)
    result = issue(cabinet(), key, requested)
    return jsonify({k: v for k, v in result.items() if k not in ('ledger', 'plan')}
                   | {'plan': result['plan'], 'ledger': result['ledger']}), 200
