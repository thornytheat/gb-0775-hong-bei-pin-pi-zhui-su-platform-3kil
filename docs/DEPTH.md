# 烘焙拼批追溯台深度登记

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 批次秤单采信 | 看毛皮重规范、重复秤单和隔离行 | `/api/lots/weighins` | `app/modules/weighins/reader.py` `app/modules/weighins/normalize.py` `app/modules/weighins/dedup.py` | `tests/test_weighins.py` |
| M2 投入来源核验 | 核对来源存量与抢占 | `/api/lots/supply` | `app/modules/supply/reservations.py` `app/modules/supply/service.py` | `tests/test_supply.py` |
| M3 拼批图比例传递 | 逐边复查产地克重、循环和待证来源 | `/api/lots/lineage` | `app/modules/lineage/cycles.py` `app/modules/lineage/mix.py` `app/modules/lineage/service.py` | `tests/test_lineage.py` |
| M4 灌装与返料归并 | 比对净重、占用和可用返料 | `/api/lots/packing` | `app/modules/packing/budget.py` `app/modules/packing/settle.py` | `tests/test_packing.py` |
| M5 重量守恒追溯 | 对平工作批、仓内和各产地账 | `/api/lots/balance` | `app/modules/balance/service.py` `app/modules/balance/stock.py` | `tests/test_balance.py` |
| M6 批次留档复跑 | 查在柜卷联、按原件复算 | `/api/runs` | `app/modules/runs/cabinet.py` `app/modules/runs/service.py` | `tests/test_runs.py` |
| M7 配方证据签发 | 从包装号取经过校验的来料证据 | `/api/export/<id>` | `app/modules/proof/recall.py` `app/modules/proof/service.py` | `tests/test_proof.py` |
| M8 交班整克卷联 | 整克取量、返料拆投守恒、逐批签发锁定 | `/api/signing/runs/<id>` `app/modules/signing/` | `app/modules/signing/grams.py` `app/modules/signing/plan.py` `app/modules/signing/service.py` `app/modules/signing/ledger.py` | `tests/test_signing.py` |

## 陷阱登记

| TRAP | 现象 | 朴素处理后果 | 本项目规则 | 位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | 投入表开头 BOM | 首字段不符 | 读取前去 BOM | `app/modules/weighins/reader.py` | `tests/test_weighins.py::test_bom` |
| TRAP-04 | 重复投入表头 | 混入投入量 | 记分段 | `app/modules/weighins/reader.py` | `tests/test_weighins.py::test_segment` |
| TRAP-05 | 秤单有多余列 | 滚入错位列 | 按原行隔离并保留工作批 | `app/modules/weighins/reader.py` | `tests/test_weighins.py::test_bad_width` |
| TRAP-07 | 投料 N/A | 当零重量 | 留原值且归为缺项 | `app/modules/weighins/normalize.py` | `tests/test_weighins.py::test_sentinel` |
| TRAP-10 | 两级拼批出现小数克 | 份额逐次截断 | 有理数逐边传播 | `app/modules/lineage/mix.py` | `tests/test_lineage.py::test_multi_parent_mix_exact` |
| TRAP-14 | 同号秤单不同重量 | 后写覆盖 | 隔离全部冲突行 | `app/modules/weighins/dedup.py` | `tests/test_weighins.py::test_conflict_removed` |
| TRAP-15 | 同包装秤单使用 kg 和 g | 重计或误判冲突 | 归一后精确判重 | `app/modules/weighins/mass.py` `app/modules/weighins/dedup.py` | `tests/test_weighins.py::test_unit_normalized_duplicate` |
| TRAP-19 | 两张返料申请独立看均够 | 按提交顺序抢占 | 总额超限则整组隔离 | `app/modules/supply/reservations.py` | `tests/test_supply.py::test_global_overdraw_no_first_claim` |
| TRAP-20 | 双向返料引用 | 顺序无法确定 | 提取循环分量和实际路径 | `app/modules/lineage/cycles.py` | `tests/test_lineage.py::test_actual_cycle` |
| TRAP-21 | 小数产地克重直接四舍五入 | 各产地相加不等于整克净重 | 最大余数法逐行整克化，每格只取 floor/ceil | `app/modules/signing/grams.py` | `tests/test_signing.py::test_allocate_rows_sums_to_whole_net` |
| TRAP-22 | 同卷返料多批各舍各的 | 领走＋留库对不平原卷 | 每卷独立成表按产地逐列守恒，留库吸收余数 | `app/modules/signing/plan.py` | `tests/test_signing.py::test_three_way_split_whole_gram_conservation` |
| TRAP-23 | 返料卷产地克重带小数 | 整克领/留数学上无法守恒 | 整卷停签并点名，相关包装进 blocked | `app/modules/signing/plan.py` | `tests/test_signing.py::test_fractional_return_roll_blocks_its_packages` |
| TRAP-24 | 后签批重算覆盖已签克重 | 已发卷联被悄悄改动 | 重算与台账逐克比对，冲突报 SIGNING_DRIFT 且保留旧卷 | `app/modules/signing/service.py` | `tests/test_signing.py::test_recompute_drift_rejects_and_keeps_old_ledger` |

## M8 交班整克卷联规则

交班只收整克卷联，分数证据（M7）保留精确份额供追溯，签发页另出一套整克账：

1. **相邻整克**：每个产地取量只允许是精确份额的 floor 或 ceil；整数份额不动。
2. **包装守恒**：包装各产地整克相加等于包装整克净重，非整克净重的包装停签。
3. **返料卷守恒**：同一卷拆投多批时，各批领走与留库的同产地整克相加等于原卷该产地克重；按卷独立做最大余数法，不跨卷凑数。
4. **逐批锁定**：`POST /api/signing/runs/<id>/issue` 只落锁本次包装对应工作批的领走记录；任何签发前都重算并与台账比对，已签克重一字不能改，冲突整单拒绝、旧版卷联保留。
5. **停签点名**：凑不平的批进 `blocked`，消息指出卡住的返料卷（如 `RT-B（FRACTIONAL_RETURN）`）与隔离秤单号。

台账写在归档柜 `signing/<run>.json`，带 sha256 校验，临时文件 + 原子替换，损坏报 `DAMAGED_LEDGER`。

库内可信核销 1670 g = 1405 g 灌装 + 75 g 损耗 + 90 g 工作批余料 + 100 g 未领返料。6 个未通过工作批合计 390 g 分批列待证。RT-A 300 g = 180 g 给 BA-02 + 120 g 给 BA-03。PK-C 275 g = 巴西 185.46 g + 哥伦比亚 17.6 g + 埃塞 71.94 g。
