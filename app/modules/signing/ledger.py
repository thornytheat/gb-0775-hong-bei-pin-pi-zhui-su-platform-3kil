"""整克卷联签发台账。

台账按归档运行（run）独立存放，追加式记录已签发包装与已锁定的返料
拆投。写入走临时文件 + 原子替换，任何一步失败都保留旧版卷联。
"""
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.errors import RoastError
from app.modules.runs.fingerprint import checksum, encode

RUN_KEY = re.compile(r'[a-f0-9]{64}\Z')


class SigningLedger:
    def __init__(self, cabinet):
        self.path = Path(cabinet.path) / 'signing'
        self.path.mkdir(parents=True, exist_ok=True)

    def _file(self, run_id):
        if not RUN_KEY.fullmatch(run_id):
            raise RoastError('NO_RUN', '称签归档不存在', 404)
        return self.path / f'{run_id}.json'

    def read(self, run_id):
        path = self._file(run_id)
        if not path.exists():
            return {'run': run_id, 'sealed': [], 'roll_locks': [], 'updated_at': None}
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise RoastError('DAMAGED_LEDGER', '签发台账不可读', 409) from exc
        if document.get('run') != run_id or document.get('checksum') != checksum(
                encode({k: v for k, v in document.items() if k != 'checksum'})):
            raise RoastError('DAMAGED_LEDGER', '签发台账核验失败', 409)
        return document

    def write(self, document):
        run_id = document['run']
        path = self._file(run_id)
        payload = {k: v for k, v in document.items() if k != 'checksum'}
        payload['updated_at'] = datetime.now(UTC).isoformat(timespec='seconds')
        payload['checksum'] = checksum(encode(payload))
        part = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path,
                                    prefix='.pending-', delete=False) as stream:
                part = Path(stream.name)
                stream.write(encode(payload))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, path)
        finally:
            if part is not None:
                part.unlink(missing_ok=True)
        return payload
