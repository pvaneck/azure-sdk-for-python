@minLength(6)
@maxLength(23)
@description('The base resource name.')
param baseName string = resourceGroup().name

@description('The location of the resource. By default, this is the same as the resource group.')
param location string = resourceGroup().location

@description('The client OID to grant access to test resources.')
param testApplicationOid string

@minLength(5)
@maxLength(50)
@description('Provide a globally unique name of the Azure Container Registry')
param acrName string = 'acr${uniqueString(resourceGroup().id)}'

@description('The latest AKS version available in the region.')
param latestAksVersion string = '1.29.8'

@description('The SSH public key to use for the Linux VMs.')
param sshPubKey string = ''

@description('The admin user name for the Linux VMs.')
param adminUserName string = 'azureuser'

@description('Whether live resources should be provisioned. Defaults to false.')
param provisionLiveResources bool = false

var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // ACR Pull

// Cluster parameters
var kubernetesVersion = '1.32.6'
var cosmosAccountName = toLower('${baseName}cosmos')
var cosmosApiVersion = '2025-05-01-preview'

var roleDefinitionId = guid(baseName, 'roleDefinitionId')
var roleAssignmentId = guid(baseName, 'roleAssignmentId')
var roleDefinitionName = 'CosmosExpandedRbacActions'

resource userAssignedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2018-11-30' = if (provisionLiveResources) {
  name: baseName
  location: location
}


resource acrPullContainerInstance 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (provisionLiveResources) {
  scope: acrResource
  name: guid(resourceGroup().id, acrPull, 'containerInstance')
  properties: {
    principalId: provisionLiveResources ? userAssignedIdentity.properties.principalId : ''
    roleDefinitionId: acrPull
    principalType: 'ServicePrincipal'
  }
}


resource acrResource 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = if (provisionLiveResources) {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

resource kubernetesCluster 'Microsoft.ContainerService/managedClusters@2023-06-01' = if (provisionLiveResources) {
  name: baseName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    enableRBAC: true
    dnsPrefix: 'identitytest'
    agentPoolProfiles: [
      {
        name: 'agentpool'
        count: 1
        vmSize: 'Standard_D8_v3'
        osDiskSizeGB: 128
        osDiskType: 'Managed'
        kubeletDiskType: 'OS'
        type: 'VirtualMachineScaleSets'
        enableAutoScaling: false
        orchestratorVersion: kubernetesVersion
        mode: 'System'
        osType: 'Linux'
        osSKU: 'Ubuntu'
      }
    ]
    linuxProfile: {
      adminUsername: adminUserName
      ssh: {
        publicKeys: [
          {
            keyData: sshPubKey
          }
        ]
      }
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
  }
}

resource cosmosAccount1 'Microsoft.DocumentDB/databaseAccounts@2025-05-01-preview' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  identity: {
    type: 'None'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    isVirtualNetworkFilterEnabled: false
    virtualNetworkRules: []
    disableKeyBasedMetadataWriteAccess: false
    enableFreeTier: false
    enableAnalyticalStorage: false
    analyticalStorageConfiguration: {
      schemaType: 'WellDefined'
    }
    databaseAccountOfferType: 'Standard'
    enableMaterializedViews: false
    capacityMode: 'Serverless'
    defaultIdentity: 'FirstPartyIdentity'
    networkAclBypass: 'None'
    disableLocalAuth: true
    enablePartitionMerge: false
    enablePerRegionPerPartitionAutoscale: false
    enableBurstCapacity: false
    enablePriorityBasedExecution: false
    defaultPriorityLevel: 'High'
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
      maxIntervalInSeconds: 5
      maxStalenessPrefix: 100
    }
    locations: [
      {
        locationName: 'West US 2'
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    cors: []
    capabilities: []
    ipRules: []
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 8
        backupStorageRedundancy: 'Geo'
      }
    }
    networkAclBypassResourceIds: []
    diagnosticLogSettings: {
      enableFullTextQuery: 'None'
    }
    capacity: {
      totalThroughputLimit: 4000
    }
  }
}

resource cosmosAccount2 'Microsoft.DocumentDB/databaseAccounts@2025-05-01-preview' = {
  name: '${cosmosAccountName}2'
  location: location
  kind: 'GlobalDocumentDB'
  identity: {
    type: 'None'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    isVirtualNetworkFilterEnabled: false
    virtualNetworkRules: []
    disableKeyBasedMetadataWriteAccess: false
    enableFreeTier: false
    enableAnalyticalStorage: false
    analyticalStorageConfiguration: {
      schemaType: 'WellDefined'
    }
    databaseAccountOfferType: 'Standard'
    enableMaterializedViews: false
    capacityMode: 'Serverless'
    defaultIdentity: 'FirstPartyIdentity'
    networkAclBypass: 'None'
    disableLocalAuth: true
    enablePartitionMerge: false
    enablePerRegionPerPartitionAutoscale: false
    enableBurstCapacity: false
    enablePriorityBasedExecution: false
    defaultPriorityLevel: 'High'
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
      maxIntervalInSeconds: 5
      maxStalenessPrefix: 100
    }
    locations: [
      {
        locationName: 'West US 2'
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    cors: []
    capabilities: []
    ipRules: []
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 8
        backupStorageRedundancy: 'Geo'
      }
    }
    networkAclBypassResourceIds: []
    diagnosticLogSettings: {
      enableFullTextQuery: 'None'
    }
    capacity: {
      totalThroughputLimit: 4000
    }
  }
}

resource cosmos1_roleDefinitionId 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2023-04-15' = {
  parent: cosmosAccount1
  name: roleDefinitionId
  properties: {
    roleName: roleDefinitionName
    type: 'CustomRole'
    assignableScopes: [
      cosmosAccount1.id
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/*'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/*'
        ]
      }
    ]
  }
}

resource cosmos1_roleAssignmentId 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2023-04-15' = {
  parent: cosmosAccount1
  name: guid(resourceGroup().id, roleAssignmentId, cosmosAccount1.id)
  properties: {
    roleDefinitionId: cosmos1_roleDefinitionId.id
    principalId: provisionLiveResources ? userAssignedIdentity.properties.principalId : ''
    scope: cosmosAccount1.id
  }
}

resource cosmos2_roleDefinitionId 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2023-04-15' = {
  parent: cosmosAccount2
  name: '${roleDefinitionId}2'
  properties: {
    roleName: roleDefinitionName
    type: 'CustomRole'
    assignableScopes: [
      cosmosAccount2.id
    ]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/*'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/*'
        ]
      }
    ]
  }
}

resource cosmos2_roleAssignmentId 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2023-04-15' = {
  parent: cosmosAccount2
  name: guid(resourceGroup().id, roleAssignmentId, cosmosAccount2.id)
  properties: {
    roleDefinitionId: cosmos2_roleDefinitionId.id
    principalId: provisionLiveResources ? userAssignedIdentity.properties.principalId : ''
    scope: cosmosAccount2.id
  }
}


output IDENTITY_USER_DEFINED_IDENTITY string = provisionLiveResources ? userAssignedIdentity.id : ''
output IDENTITY_USER_DEFINED_IDENTITY_CLIENT_ID string = provisionLiveResources ? userAssignedIdentity.properties.clientId : ''
output IDENTITY_USER_DEFINED_IDENTITY_NAME string = provisionLiveResources ? userAssignedIdentity.name : ''
output IDENTITY_AKS_CLUSTER_NAME string = provisionLiveResources ? kubernetesCluster.name : ''
output IDENTITY_AKS_POD_NAME string = provisionLiveResources ? 'python-test-app' : ''
output IDENTITY_ACR_NAME string = provisionLiveResources ? acrResource.name : ''
output IDENTITY_ACR_LOGIN_SERVER string = provisionLiveResources ? acrResource.properties.loginServer : ''
output IDENTITY_LIVE_RESOURCES_PROVISIONED string = provisionLiveResources ? 'true' : ''
output COSMOS_ACCOUNT_HOST string = reference(cosmosAccount1.id, cosmosApiVersion).documentEndpoint
output COSMOS_ACCOUNT_HOST2 string = reference(cosmosAccount2.id, cosmosApiVersion).documentEndpoint
