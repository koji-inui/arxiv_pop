# -*- coding: utf-8 -*-
# setting file
# TwitterのkeyやslackのURLの設定を行う。
# ここでは、全てAWSのパラメタストアから取得。

import boto3

ssm = boto3.client('ssm')

def parameter_aws(parameter_name):
    return ssm.get_parameters(Names=[parameter_name],WithDecryption=True)['Parameters'][0]['Value']

class Params(object):

    TWITTER_CONSUMER_KEY    = parameter_aws('arxiv_pop.TWITTER_CONSUMER_KEY')
    TWITTER_CONSUMER_SECRET = parameter_aws('arxiv_pop.TWITTER_CONSUMER_SECRET')
    TWITTER_ACCESS_TOKEN    = parameter_aws('arxiv_pop.TWITTER_ACCESS_TOKEN')
    TWITTER_ACCESS_SECRET   = parameter_aws('arxiv_pop.TWITTER_ACCESS_SECRET')

    SLACK_URL_PRIVATE       = parameter_aws('arxiv_pop.SLACK_URL_PRIVATE')
    SLACK_URL_OFFICE        = parameter_aws('arxiv_pop.SLACK_URL_OFFICE')

    S3_BUCKET_NAME          = parameter_aws('arxiv_pop.S3_BUCKET_NAME')

