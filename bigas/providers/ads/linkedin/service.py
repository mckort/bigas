"""
LinkedIn Ads service integration.

Currently this module re-exports the existing `LinkedInAdsService`
from `bigas.resources.marketing.linkedin_ads_service`. As the
codebase is decomposed further, the underlying service implementation
can be moved here without changing imports elsewhere.
"""
from bigas.resources.marketing.linkedin_ads_service import LinkedInAdsService

__all__ = ["LinkedInAdsService"]

