"""
Provides a unified abstraction of the ArchivedImage and ArchivedImageDocker tables since they're tightly related.

"""

from sqlalchemy import desc, and_, or_, func
from sqlalchemy.orm import load_only, Load, Session, lazyload, raiseload
import time

from anchore_engine import db
from anchore_engine.db import ArchivedImage, ArchivedImageDocker, CatalogImage, CatalogImageDocker, session_scope
from anchore_engine.subsys import logger
from anchore_engine.utils import epoch_to_rfc3339


# def search_archived_images(account: str, digests=None, tags=None, status=None, offset=None, limit=None):
#     """
#     Return a list of matching archived image records.
#
#     :param account:
#     :param digests:
#     :param tags:
#     :param image_status:
#     :return: list of ArchivedImage records
#     """
#
#     with session_scope() as session:
#         qry = session.query(ArchivedImage).filter(ArchivedImage.account==account).order_by(desc(ArchivedImage.last_updated))
#
#         if status is not None:
#             qry = qry.filter(ArchivedImage.status==status)
#
#         if digests:
#             qry = qry.filter(ArchivedImage.imageDigest.in_(digests))
#
#         if tags:
#             qry = qry.filter(ArchivedImage._tags.tag.in_(tags))
#
#         if offset:
#             qry = qry.offset(offset)
#
#         if limit:
#             qry = qry.limit(limit)
#
#         return [x.to_json() for x in qry.all()]


def summarize(session: Session):
    """
    Return a summary dict of counts, sizes, and last updated

    :param session:
    :return: dict
    """

    image_count = session.query(ArchivedImage).count()
    archive_bytes = session.query(func.sum(ArchivedImage.archive_size_bytes)).scalar()
    tag_count = session.query(ArchivedImageDocker).count()
    most_recent = session.query(func.max(ArchivedImage.last_updated)).scalar()

    return {
        'total_image_count': image_count,
        'total_tag_count': tag_count,
        'total_data_bytes': int(archive_bytes),
        'last_updated': epoch_to_rfc3339(most_recent)
    }


def update_image_record(session: Session, account: str, image_digest: str,  **attrs) -> ArchivedImage:
    """
    Update the kwargs fot the referenced ArchivedImage record

    :param session:
    :param account:
    :param image_digest:
    :param kwargs:
    :return:
    """
    record = session.query(ArchivedImage).filter_by(imageDigest=image_digest, userId=account).one_or_none()
    if record:
        for k, v in attrs.items():
            if hasattr(k, record):
                setattr(record, k, v)
            else:
                raise AttributeError(k)

    else:
        raise Exception('Record not found')
    
    return record


def update_image_status(session: Session, account: str, image_digest: str, old_statuses: list, new_status: str) -> str:
        current_record = session.query(ArchivedImage).filter_by(account=account, imageDigest=image_digest).options(lazyload(ArchivedImage._tags)).one_or_none()

        logger.debug('Updating archive image status from one of: {} to {} for {}/{} w/record: {}'.format(old_statuses, new_status, account, image_digest, current_record))
        if current_record:
            if current_record.status not in old_statuses:
                raise Exception("Status mismatch")
            else:
                current_record.status = new_status
        else:
            return None

        return new_status


def get(session: Session, account, image_digest):
    result = session.query(ArchivedImage).filter(or_(ArchivedImage.imageDigest==image_digest, ArchivedImage.parentDigest==image_digest), ArchivedImage.account==account).one_or_none()
    return result


def delete(session: Session, account: str, image_digests: list):
    """
    Delete one or more images by digest

    :param session:
    :param account:
    :param digests:
    :return:
    """

    # Delete the image record, cascades will handle the tags
    for result in session.query(ArchivedImage).filter(or_(ArchivedImage.imageDigest.in_(image_digests), ArchivedImage.parentDigest.in_(image_digests)), ArchivedImage.account==account):
        session.delete(result)

    return True


def get_tag_histories(session, account, registries=None, repositories=None, tags=None):
    """
    registries, repositories, and tags are lists of filter strings (wildcard '*' allowed)

    Returns a query to iterate over matches in tag sorted ascending, and tag date descending order
    :param session:
    :param account:
    :param registries:
    :param repositories:
    :param tags:
    :return: constructed query to execute/iterate over that returns tuples of (CatalogImageDocker, CatalogImage) that match userId/account and digest
    """

    select_fields = [
        ArchivedImageDocker,
        ArchivedImage
    ]

    order_by_fields = [
        ArchivedImageDocker.registry.asc(),
        ArchivedImageDocker.repository.asc(),
        ArchivedImageDocker.tag.asc(),
        ArchivedImageDocker.tag_detected_at.desc()
    ]

    qry = session.query(*select_fields).join(ArchivedImage).filter(ArchivedImage.account==account).order_by(*order_by_fields)

    for field, filters in [(ArchivedImageDocker.registry, registries), (ArchivedImageDocker.repository, repositories), (ArchivedImageDocker.tag, tags)]:
        if filters:
            wildcarded = []
            exact = []
            for r in filters:
                if r.strip() == '*':
                    continue

                if '*' in r:
                    wildcarded.append(r)
                else:
                    exact.append(r)

            conditions = []
            if wildcarded:
                for w in wildcarded:
                    conditions.append(field.like(w.replace('*', '%')))

            if exact:
                conditions.append(field.in_(exact))

            if conditions:
                qry = qry.filter(or_(*conditions))

    logger.debug('Constructed tag history query: {}'.format(qry))
    return qry
