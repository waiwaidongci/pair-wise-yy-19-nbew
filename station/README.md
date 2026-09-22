# 岩芯薄片显微样本编目与鉴定放行台

命令行台站，管理收纳箱在途单据、薄片编目补录、首次鉴定、复查放行与样本更正。
仅使用 Python 3 标准库，无第三方依赖。

## 三个业务源码单元

| 单元 | 文件 | 职责 |
| --- | --- | --- |
| 入口 | `petrography/entry.py` | CLI 参数解析、中文展示、用例编排；不含业务规则 |
| 判定 | `petrography/decision.py` | 纯领域逻辑：单据/样本/鉴定/复查/更正规则，无任何 I/O |
| 持久化 | `petrography/persistence.py` | JSONL 事件日志：仅追加、临时文件 + `os.replace` 原子落盘、重载重放 |

所有状态变更都是**不可变事件**，只追加不改写——历史稿只读由文件形态本身保证。

## 业务规则

1. **一箱一张在途单据**：箱号已有在途单据时拒绝新开（`箱号冲突`），不产生任何半成品；封箱后箱号释放。
2. **空白即停**：单据号/箱号，以及样本编号/深度/采样日期为空白时直接停止补录，不写日志。
3. **鉴定四要素**：矿物组成、结构、偏光、孔隙占比。
   - 孔隙占比 **严格大于 20%**（20.0% 放行，20.1% 复查）或**偏光缺失** → 只能进入复查队列；
   - 其余首次鉴定即放行。
4. **复查者不同**：复查署名必须与首次鉴定者不同；更正产生新稿后，复查者须与新稿鉴定者不同。复查可放行或驳回。
5. **更正重判**：样本更正立即取消旧放行，按现值重新判定并另立新稿次（v1/v2/v3…）；旧稿与所有事件在履历中只读保留，不可删除改写。
6. **重载吻合**：重启后从事件日志重放，总览、队列、履历与停用时完全一致。

## 命令

全局：`--store 路径`（默认 `station/data/events.jsonl`）、`--json`（机器可读输出）。

```bash
# 编目
python3 -m petrography open-manifest --no M-001 --box A-07
python3 -m petrography catalog --manifest M-001 --code XB-17-01 \
    --depth 1287.4m --date 2026-09-20
python3 -m petrography close-manifest --no M-001

# 首次鉴定（省略 --polarized 或传空白即偏光缺失）
python3 -m petrography appraise --code XB-17-01 --by 张工 \
    --mineral 石英、斜长石 --texture 半自形粒状 \
    --polarized 正交偏光 --porosity 12

# 复查
python3 -m petrography review --code XB-17-01 --by 李工 --verdict release   # 或 reject

# 更正（只传需要改的字段；旧放行取消、按现值重判）
python3 -m petrography correct --code XB-17-01 --by 李工 --porosity 33

# 视图
python3 -m petrography overview      # 总览：箱/单据、样本量、状态分布
python3 -m petrography queue         # 待鉴定 / 待复查队列
python3 -m petrography history --code XB-17-01   # 只读履历
```

## 测试

```bash
cd station
python3 -m unittest discover -s tests -v
```

24 个用例覆盖：箱号冲突无半成品、空白停工、孔隙阈值边界（20.0/20.1）、
偏光缺失转复查、同人复查被拒、更正取消旧放行并按现值重判、履历只读防篡改、
原子落盘无残留临时文件、重载后总览/队列/履历一致，以及子进程 CLI 冒烟。
