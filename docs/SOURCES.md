# Sources and inspiration

Primary sources read while building (13 Nov 2026):

- HubSpot KB, deduplication of records: contacts are deduplicated by email and companies by primary domain. https://knowledge.hubspot.com/records/deduplication-of-records
- HubSpot KB, associate assets and content with a campaign: "Once a contact is marked as Influenced by a campaign, the campaign influence cannot be removed." https://knowledge.hubspot.com/campaigns/associate-assets-and-content-with-a-campaign
- HubSpot KB, original and latest traffic source properties: these describe web traffic sources and are set automatically from tracking, so this agent leaves them alone and uses custom touch properties. https://knowledge.hubspot.com/properties/understand-source-properties
- ICO, electronic mail marketing: sole traders and some partnerships are treated as individuals; corporate bodies can be emailed; keep a do-not-email list. https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/

Starter scope came from my own agent spec (target list criteria, attribution rules and learnings: dedupe on domain plus company name, never overwrite first touch, estimate credits before enrichment).

Public posts seen while monitoring (text only, linked material not inspected):

- https://x.com/tojulius/status/2121024875402002885: a demo pushing badge scans, transcripts and lead scores straight to the CRM after an event. My version goes the other way: nothing reaches the CRM without a hash-bound approval.

No code was copied from any other project.
