resource "aws_sns_topic" "alarms" {
  name              = "${aws_autoscaling_group.actions-runner.name}-alarms"
  kms_master_key_id = "alias/aws/sns"
  tags              = local.default_module_tags
}

resource "aws_sns_topic_subscription" "alarm_emails" {
  for_each  = toset(var.alarm_emails)
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}
