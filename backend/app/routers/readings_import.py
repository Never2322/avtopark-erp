# -*- coding: utf-8 -*-
"""Загрузка показаний из файла: просмотр (apply=0) и загрузка (apply=1). Правила такие же, как у ручного ввода."""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from app.common import fail
from app.db import get_db
from app.deps import Principal, require
from app.services import readings_import as svc

router = APIRouter(tags=["Показания"])


@router.post("/readings/import")
def import_readings(file: UploadFile = File(...), kind: str = Form("km"), apply: str = Form("0"), legal_entity_id: int | None = Form(None),
                    p: Principal = Depends(require("readings.import")), db: Session = Depends(get_db)):
    data = file.file.read(svc.MAX_BYTES + 1)
    if len(data) > svc.MAX_BYTES:
        raise fail(413, "file_too_big", "Файл больше 3 МБ. Разбейте его на части.", "file")
    return svc.run(db, p, file.filename or "", data, default_kind=kind, legal_entity_id=legal_entity_id, apply=apply == "1")


@router.get("/readings/import/template.csv")
def template(p: Principal = Depends(require("readings.import"))):
    body = "\ufeff" + svc.TEMPLATE
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pokazaniya-obrazec.csv"'})
