# 实验目录说明

data/test_dataset.csv：实验数据集，共 900 条样本，包含输入、测试组标记、是否期望异常，以及期望 subtotal、member_discount、coupon_discount、shipping、payable

result：实验输出结果，包括 CSV 和 PNG 图表

order_pricing.py：被测订单结算模块

run_experiment.py：读取数据集、执行测试、注入缺陷、统计覆盖率并生成图表
