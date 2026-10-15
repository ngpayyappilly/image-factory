output "github_actions_variables" {
  description = "Set these as GitHub repository variables. The workflows sign in to Azure only when AZURE_TENANT_ID is set."
  value = {
    AZURE_TENANT_ID          = azurerm_user_assigned_identity.builder.tenant_id
    AZURE_SUBSCRIPTION_ID    = var.subscription_id
    AZURE_BUILDER_CLIENT_ID  = azurerm_user_assigned_identity.builder.client_id
    AZURE_PROMOTER_CLIENT_ID = azurerm_user_assigned_identity.promoter.client_id
  }
}

output "smoke_test_subnet_ids" {
  description = "config/images.yaml: platforms.azure.smoke_test.subnet_ids"
  value       = { for r, s in azurerm_subnet.smoke : r => s.id }
}

output "consumer_role_definition_id" {
  description = "Assign to teams/principals that deploy VMs from golden images"
  value       = azurerm_role_definition.consumer.role_definition_resource_id
}

output "image_definitions" {
  value = keys(local.flavors)
}
