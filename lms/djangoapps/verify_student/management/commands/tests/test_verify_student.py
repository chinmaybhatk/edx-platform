"""
Tests for django admin commands in the verify_student module

Lots of imports from verify_student's model tests, since they cover similar ground
"""


import boto
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from mock import patch
from testfixtures import LogCapture

from common.test.utils import MockS3BotoStorageMixin
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, SSPVerificationRetryConfig
from lms.djangoapps.verify_student.tests.test_models import (
    FAKE_SETTINGS,
    mock_software_secure_post,
    mock_software_secure_post_error
)
from student.tests.factories import UserFactory  # pylint: disable=import-error, useless-suppression

LOGGER_NAME = 'retry_photo_verification'


# Lots of patching to stub in our own settings, and HTTP posting
@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post)
class TestVerifyStudentCommand(MockS3BotoStorageMixin, TestCase):
    """
    Tests for django admin commands in the verify_student module
    """
    def create_and_submit(self, username):
        """
        Helper method that lets us create new SoftwareSecurePhotoVerifications
        """
        user = UserFactory.create()
        attempt = SoftwareSecurePhotoVerification(user=user)
        user.profile.name = username
        attempt.upload_face_image("Fake Data")
        attempt.upload_photo_id_image("More Fake Data")
        attempt.mark_ready()
        attempt.submit()
        return attempt

    def test_retry_failed_photo_verifications(self):
        """
        Tests that the task used to find "must_retry" SoftwareSecurePhotoVerifications
        and re-submit them executes successfully
        """
        # set up some fake data to use...
        self.create_and_submit("SuccessfulSally")
        with patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post_error):
            self.create_and_submit("RetryRoger")
        with patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post_error):
            self.create_and_submit("RetryRick")
        # check to make sure we had two successes and two failures; otherwise we've got problems elsewhere
        assert len(SoftwareSecurePhotoVerification.objects.filter(status="submitted")) == 1
        assert len(SoftwareSecurePhotoVerification.objects.filter(status='must_retry')) == 2
        call_command('retry_failed_photo_verifications')
        attempts_to_retry = SoftwareSecurePhotoVerification.objects.filter(status='must_retry')
        assert not attempts_to_retry

    def test_args_from_database(self):
        """Test management command arguments injected from config model."""
        # Nothing in the database, should default to disabled

        with LogCapture(LOGGER_NAME) as log:
            call_command('retry_failed_photo_verifications', '--args-from-database')
            log.check_present(
                (
                    LOGGER_NAME, 'WARNING',
                    u"SSPVerificationRetryConfig is disabled or empty, but --args-from-database was requested."
                ),
            )
        # Add a config
        config = SSPVerificationRetryConfig.current()
        config.arguments = '--verification-ids 1 2 3'
        config.enabled = True
        config.save()

        with patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post_error):
            self.create_and_submit("RetryRoger")

            with LogCapture(LOGGER_NAME) as log:
                call_command('retry_failed_photo_verifications')

                log.check_present(
                    (
                        LOGGER_NAME, 'INFO',
                        u"Attempting to retry {0} failed PhotoVerification submissions".format(1)
                    ),
                )

        with LogCapture(LOGGER_NAME) as log:
            call_command('retry_failed_photo_verifications', '--args-from-database')

            log.check_present(
                (
                    LOGGER_NAME, 'INFO',
                    u"Fetching retry verification ids from config model"
                ),
            )
