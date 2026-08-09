# Official source snapshots

The structured corpus uses derived text artifacts. Each derived artifact must be
paired with the matching official PDF snapshot before strict corpus validation
can pass.

Required official sources:

- Federal Decree-Law No. 33 of 2021, Arabic: https://uaelegislation.gov.ae/ar/legislations/1541/download
- Federal Decree-Law No. 33 of 2021, English/current amended PDF: https://www.mohre.gov.ae/assets/download/e82f7872/Federal%20Decree-Law%20No.%2033%20of%202021%20Regarding%20the%20Regulation%20of%20Employment%20Relationship%20and%20its%20amendments_638990571068264034.pdf.aspx
- Cabinet Resolution No. 1 of 2022, Arabic: https://uaelegislation.gov.ae/ar/legislations/1547/download
- Cabinet Resolution No. 1 of 2022, English: https://uaelegislation.gov.ae/en/legislations/1547/download

The current environment could not retrieve these PDFs because the official
endpoints returned HTTP 403 or timed out. Do not replace them with placeholders.
Once downloaded, add the four `raw_pdf` manifest records, calculate SHA-256
from the bytes on disk, and rerun the strict corpus tests.

