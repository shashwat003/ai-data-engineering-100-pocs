# Results (measured by `python -m multimodal_doc_qa.main`; 4 synthetic reports x 4 pages, reportlab PDFs, exact match)

| question kind | extraction | correct | n | accuracy |
|---|---|---|---|---|
| text_open | naive | 6 | 24 | 0.250 |
| text_open | layout | 24 | 24 | 1.000 |
| text_staff | naive | 0 | 24 | 0.000 |
| text_staff | layout | 24 | 24 | 1.000 |
| table_cell | naive | 70 | 86 | 0.814 |
| table_cell | layout | 86 | 86 | 1.000 |
| table_gap | naive | 1 | 10 | 0.100 |
| table_gap | layout | 10 | 10 | 1.000 |

- pages whose extracted text still contains the running header: {'naive': 16, 'layout': 0}
- correct table answers carrying the right page citation: 86/86
- `table_gap` = questions whose cell is empty in the PDF; the correct answer is *no answer*.
