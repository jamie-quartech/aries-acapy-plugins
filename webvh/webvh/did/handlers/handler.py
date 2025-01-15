"""Handler for endorsement operations."""

import logging

from acapy_agent.messaging.base_handler import BaseHandler
from acapy_agent.messaging.request_context import RequestContext
from acapy_agent.messaging.responder import BaseResponder
from acapy_agent.vc.data_integrity.manager import DataIntegrityManager
from acapy_agent.vc.data_integrity.models.options import DataIntegrityProofOptions
from acapy_agent.wallet.keys.manager import MultikeyManager

from ..endorsement_manager import EndorsementManager
from ..messages.endorsement import EndorsementRequest, EndorsementResponse
from ..operations_manager import DidWebvhOperationsManager
from ..utils import get_url_decoded_domain

LOGGER = logging.getLogger(__name__)


class EndorsementRequestHandler(BaseHandler):
    """Message handler class for endorsement requests."""

    async def _handle_auto_endorse(
        self,
        context: RequestContext,
        responder: BaseResponder,
        proof: dict,
        document: dict,
        parameters: dict,
    ):
        """Handle automatic endorsement."""
        domain = proof.get("domain")
        url_decoded_domain = get_url_decoded_domain(domain)

        async with context.profile.session() as session:
            # Attempt to get the endorsement key for the domain
            if not await MultikeyManager(session).kid_exists(url_decoded_domain):
                # If the key is not found, return an error
                LOGGER.error(
                    f"Endorsement key not found for domain: {url_decoded_domain}. The "
                    "administrator must add the key to the wallet that matches the key on"
                    " the server."
                )
                return

            # If the key is found, perform endorsement
            endorsement_key_info = await MultikeyManager(session).from_kid(
                url_decoded_domain
            )
            endorsed_document = await DataIntegrityManager(session).add_proof(
                document,
                DataIntegrityProofOptions(
                    type="DataIntegrityProof",
                    cryptosuite="eddsa-jcs-2022",
                    proof_purpose="assertionMethod",
                    verification_method=f"did:key:{endorsement_key_info.get('multikey')}#{endorsement_key_info.get('multikey')}",
                    expires=proof.get("expires"),
                    domain=domain,
                    challenge=proof.get("challenge"),
                ),
            )
        # If the endorsement is successful, return a success message
        await responder.send(
            message=EndorsementResponse(
                state="posted",
                document=endorsed_document,
                parameters=parameters,
            ),
            connection_id=context.connection_record.connection_id,
        )

    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Message handler logic for endorsement requests."""
        assert isinstance(context.message, EndorsementRequest)
        self._logger.debug(
            "Received endorsement request: %s",
            context.message.document,
        )

        document = context.message.document
        proof = document.get("proof")
        if not proof:
            LOGGER.error("No proof found in log entry")
            return
        proof = proof[0]

        if (
            context.profile.settings.get("plugin_config", {})
            .get("did-webvh", {})
            .get("auto_endorse")
        ):
            await self._handle_auto_endorse(
                context, responder, proof, document, context.message.parameters
            )
        else:
            LOGGER.info(
                "Auto endorsement is not enabled. The administrator must manually "
                "endorse the log entry."
            )
            # Save the log entry to the wallet for manual endorsement
            await EndorsementManager(context.profile).save_log_entry(
                document, connection_id=context.connection_record.connection_id
            )
            await responder.send(
                message=EndorsementResponse(
                    state="pending",
                    document=document,
                    parameters=context.message.parameters,
                ),
                connection_id=context.connection_record.connection_id,
            )


class EndorsementResponseHandler(BaseHandler):
    """Message handler class for endorsement responses."""

    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Message handler logic for endorsement responses."""
        self._logger.info(
            "Received endorsement response: %s",
            context.message.state,
        )
        assert isinstance(context.message, EndorsementResponse)

        await DidWebvhOperationsManager(context.profile).finish_create(
            context.message.document,
            state=context.message.state,
            parameters=context.message.parameters,
        )