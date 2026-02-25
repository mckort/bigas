"""
LinkedIn Ads provider module.

For now this simply re-exports the concrete provider defined in
`bigas.providers.ads.linkedin`. Over time, LinkedIn-specific service
and endpoint logic can be moved into this subpackage.
"""
from bigas.providers.ads.linkedin import LinkedInAdsProvider

__all__ = ["LinkedInAdsProvider"]

