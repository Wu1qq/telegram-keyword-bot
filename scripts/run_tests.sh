#!/bin/bash

# 运行测试并生成覆盖率报告
pytest --cov=. --cov-report=html

# 如果测试失败则退出
if [ $? -ne 0 ]; then
    echo "测试失败"
    exit 1
fi

# 显示覆盖率报告摘要
coverage report

echo "测试完成，详细覆盖率报告已生成到 htmlcov 目录" 