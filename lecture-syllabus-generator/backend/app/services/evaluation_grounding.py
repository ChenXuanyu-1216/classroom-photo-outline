"""Reviewed reference cards for the narrow classification-output topic.

Not a general fact checker. The caller validates every model reference first.
No runtime web request or additional model call. Reviewed 2026-10-23.
"""
import re

from app.services.model_output import ExpandedPoint


OUTPUT_METRICS_REFERENCE = (
    '分类评价须区分类别标签、决策分数和概率：只需真实类别与预测类别即可计算准确率、精确率、召回率、F1 与混淆矩阵，'
    '不要求模型输出概率。二分类 ROC-AUC 可使用未阈值化的决策分数或概率；多分类需按具体定义与接口要求准备各类别输出，'
    '不能默认任意分数均被接受。对数损失需要合法的概率预测，但不要求概率已校准；未经校准的概率仍可计算对数损失，'
    '任意原始分数不能直接冒充概率。校准曲线比较预测概率与实际频率；拟合校准器则可将原始决策分数或概率估计映射为概率。'
    '这些条件决定应选择哪一层输出进行评价。依据 scikit-learn 官方文档整理：'
    'https://scikit-learn.org/stable/modules/model_evaluation.html；'
    'https://scikit-learn.org/stable/modules/calibration.html'
)

MODEL_DIMENSIONS_REFERENCE = (
    '概率/非概率、生成式/判别式、参数/非参数是不同划分维度。概率模型可对联合分布或条件分布建模：'
    '逻辑回归直接建模 P(y|x)，是概率判别模型；朴素贝叶斯对联合分布 P(x,y) 或 P(x|y)P(y) 建模再求后验，'
    '属于生成式概率模型，不能把概率模型限定为只直接学习 P(y|x)。'
    '通常的 SVM 决策函数输出分数而非直接输出概率；非概率模型仍能预测类别并计算标签指标，有分数时也可进行相应排序评价。'
    '不能把所有非概率模型概括为只使用决策边界附近样本。依据 scikit-learn 官方模型说明整理：'
    'https://scikit-learn.org/stable/modules/linear_model.html#logistic-regression；'
    'https://scikit-learn.org/stable/modules/naive_bayes.html'
)


def _classification_output_topic(point):
    text = re.sub(r'\s+', '', point.content).lower()
    return (point.source_type == '课堂原话'
            and re.search(r'分类|监督学习', text)
            and re.search(r'概率模型|非概率|p\(y\|x\)', text)
            and re.search(r'评估|评价|指标', text))


def ground_evaluation_points(points, point_map):
    """Replace only additions actually anchored to this reviewed topic."""
    result = []
    references = None
    for point in points:
        ids = [item for item in point.based_on_ids if _classification_output_topic(point_map[item])]
        if not ids:
            result.append(point)
            continue
        if references is None:
            references = [ExpandedPoint(content=content, kp_type='concept', based_on_ids=list(ids))
                          for content in (OUTPUT_METRICS_REFERENCE, MODEL_DIMENSIONS_REFERENCE)]
            result.extend(references)
        else:
            for reference in references:
                reference.based_on_ids.extend(item for item in ids if item not in reference.based_on_ids)
    return result
