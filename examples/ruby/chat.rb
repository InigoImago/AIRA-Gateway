# RubyLLM through AIRA: one answer, a streamed one, an embedding.
#
#   bundle install && AUTH=key bundle exec ruby chat.rb   # or: bearer, service-account, token-as-key
#
# RubyLLM's Gemini base URL includes the API version: <gateway>/uc/<use-case>/v1beta. A chat takes
# headers (with_headers); an embedding takes none, so a token goes in the key setting.
require 'json'
require 'net/http'
require 'ruby_llm'

def env(name, fallback = nil)
  value = ENV.fetch(name, '')
  return value unless value.empty?

  fallback || raise("#{name} is not set")
end

# Keycloak's client-credentials grant: a confidential client signs in as itself.
def service_account_token
  response = Net::HTTP.post_form(URI(env('TOKEN_URL')), grant_type: 'client_credentials',
                                                        client_id: env('CLIENT_ID'),
                                                        client_secret: env('CLIENT_SECRET'))
  raise "token endpoint: #{response.code} #{response.body}" unless response.is_a?(Net::HTTPSuccess)

  JSON.parse(response.body).fetch('access_token')
end

key = nil   # the key setting: an AIRA API key, or a token where no header can be set
token = nil # a token sent as Authorization: Bearer
case env('AUTH', 'key')
when 'key' then key = env('API_KEY') # an AIRA API key, issued in the console: use case -> API keys
when 'bearer' then token = env('ACCESS_TOKEN') # a person's Keycloak access token
when 'service-account' then token = service_account_token # a service account's token
when 'token-as-key' then key = env('ACCESS_TOKEN', '').then { |t| t.empty? ? service_account_token : t }
else raise "unknown AUTH #{ENV.fetch('AUTH')}"
end

RubyLLM.configure do |config|
  config.gemini_api_key = key || token # with a bearer token, the key is the token as well
  config.gemini_api_base = "#{env('GATEWAY_URL')}/uc/#{env('USE_CASE')}/v1beta"
end
headers = token ? { 'Authorization' => "Bearer #{token}" } : {}
model = env('MODEL', 'gemini-2.5-flash')
new_chat = -> { RubyLLM.chat(model: model, provider: :gemini, assume_model_exists: true).with_headers(headers) }

puts new_chat.call.ask('What is an API gateway?').content
new_chat.call.ask('Count to five.') { |chunk| print chunk.content }
puts

embedding = RubyLLM.embed('How high is the Zugspitze?', model: env('EMBEDDING_MODEL', 'gemini-embedding-001'),
                                                        provider: :gemini, assume_model_exists: true)
puts "embedding: #{embedding.vectors.size} dimensions"
