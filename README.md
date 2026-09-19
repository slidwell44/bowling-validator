# Bowling Validator

Bowling Validator is a Python project for receiving substitute-bowler requests
from Gmail and automating their acceptance.

The active application lives in
[bowling-email-subscriber](bowling-email-subscriber/). It receives Gmail
notifications through Google Pub/Sub, reads new inbox messages, and provides the
foundation for future form automation.

See the [application README](bowling-email-subscriber/README.md) for the
service overview and [Google Cloud setup](bowling-email-subscriber/docs/google-cloud.md)
for deployment and mailbox configuration.
