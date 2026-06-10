// Fix My Vibe — Azure infrastructure
// Provisions Azure AI Foundry project and model deployments.
// Deploy with: azd provision

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment (used for resource group and resource naming)')
param environmentName string

@minLength(1)
@description('Primary location for all resources')
param location string

var resourceGroupName = 'rg-${environmentName}'

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resourceGroupName
  location: location
}

// TODO: Add Microsoft.CognitiveServices/accounts resource (kind: AIServices)
// TODO: Add o4-mini model deployment under that account
// TODO: Output the endpoint URL for use in .env / azd env vars
// See: https://learn.microsoft.com/azure/ai-services/openai/how-to/create-resource-bicep
